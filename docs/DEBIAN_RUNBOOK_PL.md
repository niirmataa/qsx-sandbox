# Alpine QSX: lokalne budowanie i odbiór na Debianie 13

Ta iteracja dostarcza samodzielne `qsx-alpine`: receptura zadania, jawny profil
agenta, przypięte wejścia, obraz i niezależna instancja. Kod historycznego QSX
pozostaje na `archive/qsx-core-pr1-3eb4908`; nie jest zależnością wykonawczą.
Gałąź pracy: `feat/alpine-task-images`, punkt początkowy
`88abbbb451e63e3179f87a88192c7e95042333bd`.

Obrazy buduje operator lokalnie. Testy wykonane w runnerze nie oznaczają rozruchu
Alpine na Twoim sprzęcie. Scenariusz poniżej zapisuje rzeczywiste wyniki każdego
polecenia w `demo-report.json`; niespełnione warunki kończą przebieg błędem.
Nie instaluje pakietów Debiana, nie nadaje ACL, nie zmienia firewalla i nie loguje
operatora do usługi modelowej.

## 1. Narzędzia hosta i uprawnienia

Sprawdź architekturę x86_64, włączoną w firmware wirtualizację i dostęp operatora
do `/dev/kvm`. Przegląd instalacji jest osobnym krokiem:

```sh
apt-get --simulate install qemu-system-x86 qemu-utils libvirt-clients \
  libvirt-daemon-system xorriso python3-venv git gpg acl
```

Po lokalnej akceptacji tego zakresu operator może wykonać:

```sh
sudo apt-get update
sudo apt-get install qemu-system-x86 qemu-utils libvirt-clients \
  libvirt-daemon-system xorriso python3-venv git gpg acl
```

Pakiet `libvirt-daemon-system` składa standardową lokalną instalację QEMU/libvirt;
`qemu-utils` dostarcza narzędzia obrazów. Źródła:
[Debian libvirt-daemon-system](https://packages.debian.org/trixie/libvirt-daemon-system),
[Debian qemu-utils](https://packages.debian.org/trixie/qemu-utils).

Potrzebny jest także **Cosign 3.1.3** do weryfikacji pochodzenia wejść. Przygotuj go
z [oficjalnego wydania](https://github.com/sigstore/cosign/releases/tag/v3.1.3),
stosując [procedurę sprawdzania podpisów instalatora](https://docs.sigstore.dev/cosign/system_config/installation/).
SHA-256 sprawdzonego `cosign-linux-amd64` tego wydania:
`4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71`.
Sam zgodny hash nie zastępuje weryfikacji pochodzenia. Nie używaj `latest` ani
wyłączenia kontroli TLS/podpisów jako sposobu naprawy błędu.

```sh
cosign version --json
test -r /dev/kvm && test -w /dev/kvm
virsh --connect qemu:///system list --all
```

Operator wymaga zatwierdzonego dostępu do KVM i systemowego libvirt. Dodanie do
grupy `libvirt` daje istotne uprawnienia zarządzania hostem i należy do lokalnego
przeglądu administratora; skrypt demonstracyjny tego nie robi.

## 2. Instalacja dostarczonego CLI

W katalogu dostarczonego repozytorium zbuduj i zainstaluj wheel. Runtime QSX
korzysta z biblioteki standardowej; budowa wheela nie wykonuje zadań badawczych.

```sh
python3 -m venv .venv
.venv/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
QSX_VENV="$HOME/.local/share/qsx-alpine/venv"
python3 -m venv "$QSX_VENV"
"$QSX_VENV/bin/python" -m pip install --no-index --no-deps dist/qsx_alpine-*.whl
"$QSX_VENV/bin/qsx-alpine" --version
"$QSX_VENV/bin/qsx-alpine" doctor
```

Opcjonalne testy deweloperskie na standardowym Pythonie 3.13 z Debiana 13 używają
osobnego locka. Wskaż rozpakowany dostarczony wheelhouse:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --no-index --require-hashes \
  --find-links /sciezka/qsx-dev-wheelhouse/cp313 -r requirements-dev-py313.lock
PYTHONPATH=src .venv/bin/python -m pytest -q
```

`requirements-dev.lock` dotyczy Pythona 3.12. Dla 3.13 sprawdzono rozwiązanie i
integralność zależności; wykonanie testów na interpreterze 3.13 pozostaje lokalnym
odbiorem. Nie przedstawiaj wyników runnera z 3.12 jako wyników Debiana 13.

## 3. Plan i przygotowanie rzeczywistych obrazów

Uruchomienie skryptu bez `--run` lub `--continue` tylko wypisuje plan. Ścieżki cache
i wyników muszą być oddzielne i leżeć poza repozytorium. `--run` wymaga nowego,
nieistniejącego katalogu wyników. Katalog cache można wykorzystywać ponownie.

```sh
python3 scripts/demo_debian.py --plan \
  --cache /srv/qsx/cache --work-dir /srv/qsx/demo-001 \
  --qsx "$QSX_VENV/bin/qsx-alpine"

python3 scripts/demo_debian.py --run --prepare-only \
  --cache /srv/qsx/cache --work-dir /srv/qsx/demo-001 \
  --qsx "$QSX_VENV/bin/qsx-alpine"
```

Najpierw administrator przygotowuje operatorowi możliwość tworzenia własnych
katalogów w wybranym `/srv/qsx`; nie uruchamiaj całego buildera jako root w celu
ominięcia błędu dostępu. Możesz wybrać inne dedykowane miejsce po przeglądzie
ścieżek wymaganych przez libvirt.

Przygotowanie sprawdza starter z profilem Codex, tworzy nową recepturę z `python3`
i profilem `agent-profiles/python-shell.toml`, zamraża wejścia i buduje przez KVM.
Ponowne użycie tego samego locka/cache publikuje `starter-cached.qcow2` do **nowej
ścieżki**. Wymaga `cache_hit=true`, zgodnych hashy obu publikacji i udanej
weryfikacji obrazów. To porównanie zachowanego wyniku, nie deklaracja identyczności
bajtowej dwóch niezależnych kompilacji. `image-build` nie wykonuje pobrań;
`template-lock` jest jawnym etapem pozyskania wejść.

Powstają dwa niezależne dyski instancji, syntetyczny katalog projektu i osobna
polityka operatora. Ich dokładne ścieżki podaje stdout oraz `demo-state.json`.
Pierwsze przygotowanie ma status `AWAITING_HOST_STORAGE_ACCESS`, nie ukończony
odbiór hosta.

## 4. Jawny przegląd DAC/ACL przed startem instancji

Systemowe QEMU działa pod kontem usługi, standardowo `libvirt-qemu`. Chronione
katalogi mają tryb 0700, dyski 0600. Administrator musi świadomie dopuścić konto
usługi do konkretnych dysków i nośników; uprawnienia operatora do libvirt nie
rozwiązują tego automatycznie.

Sprawdź faktyczne konto QEMU, wszystkie katalogi nadrzędne i lokalną konfigurację
AppArmor. Poniższy przykład dotyczy **wyłącznie dwóch świeżych instancji tego
przebiegu**, przy standardowym koncie `libvirt-qemu`:

```sh
QSX_RUN=/srv/qsx/demo-001
getent passwd libvirt-qemu
namei -l "$QSX_RUN/instances"
sudo setfacl -m u:libvirt-qemu:--x /srv/qsx "$QSX_RUN" "$QSX_RUN/instances"
for QSX_INSTANCE in "$QSX_RUN"/instances/*; do
  sudo setfacl -m u:libvirt-qemu:--x "$QSX_INSTANCE"
  sudo setfacl -m u:libvirt-qemu:rw- "$QSX_INSTANCE/disk.qcow2"
  sudo setfacl -m u:libvirt-qemu:r-- "$QSX_INSTANCE/import.iso"
  getfacl "$QSX_INSTANCE" "$QSX_INSTANCE/disk.qcow2" "$QSX_INSTANCE/import.iso"
done
```

Wykonanie przykładu wymaga lokalnej akceptacji administratora. Przy innej ścieżce
uzupełnij dokładnie jej wymagane katalogi nadrzędne. Nie stosuj `chmod -R 777`,
udostępniania katalogu domowego/projektu ani wyłączania AppArmor. Odmowa AppArmor
wymaga przeglądu konkretnej definicji domeny i lokalnej polityki. Same ACL nie
potwierdzają kompletnej izolacji hosta.

## 5. Kontynuacja tego samego przebiegu

```sh
python3 scripts/demo_debian.py --continue --work-dir /srv/qsx/demo-001 \
  --qsx "$QSX_VENV/bin/qsx-alpine"
```

Skrypt wykonuje publiczne komendy `start`, odczekuje rzeczywistą gotowość importu,
wywołuje `finalize-import` (kontrola gościa, zatrzymanie i odłączenie ISO), a potem
ponownie uruchamia instancję. Przed zadaniem ponawia wyłącznie nieszkodliwe `true`
przez `run --timeout 10 -- true`, do ograniczonego czasu gotowości powłoki.
Właściwego zadania nie ponawia automatycznie po timeout. Uruchamia prosty test Pythona jako użytkownik
nie-root. Zapis w pierwszym gościu nie może pojawić się w drugim ani w źródle;
hash obrazu wzorcowego pozostaje zgodny.

Następnie `network-cut` odcina wyjście poza gościem, a kolejne polecenie sprawdza
działanie lokalnej konsoli szeregowej. Ten test potwierdza odcięcie i dostępność
transportu dla domyślnie offline instancji. Nie jest testem odrzucenia istniejącej
sesji internetowej ani audytem wszystkich ścieżek sieciowych hosta.

Opcjonalnie `--interactive` otwiera dodatkowo lokalne `console` i `agent`.
Z konsoli libvirt wychodzi się `Ctrl+]`, z agenta Python `Ctrl+D`.
Bez tej opcji skrypt nie czeka na interakcję człowieka. Poprawny przebieg kończy
się kontrolowanym zatrzymaniem obu gości i raportem
`PASS_LOCAL_DEMO_WITH_EXPLICIT_EXCLUSIONS`.

Można użyć od razu `--run` bez `--prepare-only`, jeżeli lokalne dopuszczenie storage
zostało poprawnie zapewnione. Odmowa dostępu kończy wtedy przebieg na konkretnym
kroku; nie uruchamia alternatywnego hypervisora ani procesu jako root.

Raport zachowuje czasy poleceń, dane rozmiarów obrazu, `cache_hit`, obserwacje
gościa i błędy. `demo-state.json` wskazuje locki, obrazy i instancje. Nie usuwaj
artefaktów po błędzie; sprawdź ostatni krok oraz `status` właściwej instancji.
Ponowne `--continue` sprawdza rzeczywisty `status` i `import_finalized`: nie próbuje
ponownie uruchomić działającej VM ani wykonać zakończonego importu. Dotyczy to tego
syntetycznego scenariusza z bezpiecznym ponowieniem sprawdzenia i zapisu tego samego
znacznika. Nie jest ogólnym mechanizmem wznawiania dowolnej pracy badawczej.

## 6. Model, logowanie i przekazanie do Astra CLI

Domyślny profil sieci jest offline. Adapter egzekwujący wyjście do OpenAI nie jest
zaimplementowany w tej iteracji; żądanie takiego profilu kończy się odmową.
`codex --version` i sprawdzenie opcji device auth w obrazie nie oznaczają pracy
modelu. Nie włączaj ogólnego NAT-u ani samego proxy jako zamiennika allowlisty.

Wynik modelu pozostaje `NOT_RUN`, logowanie `AWAITING_OPERATOR_LOGIN`.
Wspierany login operator wykona dopiero po dostarczeniu i sprawdzeniu zatwierdzonego
adaptera wyjścia. Nie kopiuj poświadczeń hosta do wzorca, obrazu ani raportów.
Opcjonalny agent demonstracji to jawnie wybrana powłoka Python, bez logowania.

Astra CLI otrzymuje to repo, źródłowy bundle historii, locki/manifesty i tę
instrukcję. Powinna najpierw przeczytać bieżący zakres w README, wykonać plan,
przejrzeć instalację i DAC/ACL, a następnie uruchomić demo lokalnie. Zwykłe błędy
implementacji/integracji naprawia w tej gałęzi wraz z regresją. Braki sprzętu,
uprawnień, logowania i brakujący adapter sieci opisuje oddzielnie. Nie wykonuje
merge i nie przedstawia testów kontraktów z fikcyjnym CLI jako rozruchu VM.

Checkpoint/rollback/Research Memory historycznego kontrolera pozostają poza
samodzielną iteracją obrazów. Ich obecność w archiwalnej gałęzi nie oznacza
dostępności tych komend w `qsx-alpine`. Archiwalna dokumentacja pozostaje zachowana.
