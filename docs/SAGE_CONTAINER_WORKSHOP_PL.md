# SageMath + Lean 4 w kontenerze wewnątrz VM Alpine

Status odbioru z 2026-09-08: `FAIL` — rzeczywisty build wykonano; zatrzymał
się na integracji lokalnego sandboxu Codexa z usługą narzędzi. Dokładne
obliczenia Sage oraz kompilacja Lean i Lake przeszły w gościu jako nie-root.
Nie powstał zaakceptowany obraz Sage z manifestem. Osobna receptura
`templates/sage-lean4-container.toml` dostarcza SageMath 10.8 i Lean 4.33.1
z Lake; pełny wynik wymaga poprawki integracji i ponownego rzeczywistego builda.
Natywna receptura `sage-lean4.toml` zachowuje status eksperymentalny.

Dostępny host Ubuntu 24.04.4 ma około 15 GiB pamięci fizycznej i przy
rozpoznaniu około 9 GiB dostępnej. Żądanie 16 GiB starej receptury natywnej
nie mieści się na nim. W osobnym wariancie jawnie dobrano 4096 MiB, 4 vCPU
oraz dysk 32 GiB, mieszczące się w domyślnej polityce. To konfiguracja do
pomiaru, nie wynik pomiaru rzeczywistego zużycia. Nie zmieniono polityki
ani zasobów receptury natywnej. Startowy limit budowy wynosi 3600 sekund.

VM nadal uruchamia Alpine 3.24.1. Docker działa wyłącznie w tej VM. Narzędzia
używają glibc z obrazu wydawcy Sage, którego konfiguracja wskazuje Ubuntu
22.04. Docker 29.5.3-r1 z zamrożonego APK używa jawnie klasycznego
`overlay2` (`containerd-snapshotter=false`): pozwala zachować identyfikację
przez digest konfiguracji i nie przechowuje drugiej, skompresowanej kopii
warstw na dysku VM. Docker 29 domyślnie wybiera inny magazyn z inną semantyką
ID; [dokumentacja magazynów](https://docs.docker.com/engine/storage/containerd/)
opisuje także różnicę miejsca. Konfiguracja storage jest częścią tej jednej
receptury, a build sprawdza rzeczywisty driver. Lean pochodzi z oficjalnego
binarnego archiwum Linux; jest montowany
z gościa tylko do odczytu do tego samego kontenera. Nie jest to natywna
kompilacja Sage lub Lean na musl. Mathlib nie jest dołączony.

## Przypięte źródła i freeze

Źródłem Sage jest [repozytorium obrazów wydawcy](https://hub.docker.com/r/sagemath/sagemath/tags),
wersja 10.8 dla linux/amd64. Utrwalony manifest OCI ma digest
`sha256:fb96c7fb10d672f3be0d088399db3aeac52650416d1bb71f74f26429ea35778f`.
SHA-256 konfiguracji, czyli identyfikator obrazu Dockera, wynosi
`aeef59a8c17212357aeb83bd7f1cfac43bb1c789ae3fa2d887227c763bdfcac3`.
Receptura przypina sam manifest, konfigurację i wszystkie unikatowe warstwy.
Podczas pracy wrapper wybiera identyfikator konfiguracji, nigdy zmienny tag.

[Wydanie Lean 4.33.1](https://github.com/leanprover/lean4/releases/tag/v4.33.1)
publikuje `lean-4.33.1-linux.tar.zst`, 570405234 B, SHA-256
`890afd185370f85666025b883914ab4f4b339136f8c96167b69cfb62aecaf235`.
Hash został sprawdzony z metadanymi oficjalnego API wydania i jest weryfikowany
ponownie po pobraniu, podczas freeze QSX i wewnątrz buildera.

Registry wymaga krótkotrwałego anonimowego tokenu odczytu. Zwykłe pole HTTPS
receptury nie przechowuje tego tokenu. Wstępny skrypt pobiera go tylko do
pamięci procesu, sprawdza przypięte bajty i wpisuje je do istniejącego formatu
cache `blobs/SHA256.artifact`. Przy przekierowaniu usuwa nagłówek autoryzacji
oraz wymaga HTTPS. Nie używa poświadczeń operatora, hostowego Dockera ani
kodu z obrazu. Nie rozpakowuje warstw na hoście. Przy braku cache potrzebny
jest ten jawny krok przed `template-lock`; sam anonimowy URL registry nie
obsługuje automatycznie jego protokołu uwierzytelniania.

Z katalogu repo, po przygotowaniu venv i narzędzia:

```sh
.venv/bin/python scripts/freeze_sage_container.py \
  --cache /TRWALY/KATALOG/qsx-input-cache \
  --output /TRWALY/KATALOG/sage-freeze-01
qsx-alpine template-check templates/sage-lean4-container.toml
qsx-alpine template-lock templates/sage-lean4-container.toml \
  --cache /TRWALY/KATALOG/qsx-input-cache
```

`--output` musi być nowym katalogiem poza repo. Powstają `FREEZE.json`,
`SHA256SUMS` i deterministyczne archiwum
`sagemath-10.8-linux-amd64.oci.tar`. Archiwum ma układ OCI oraz dodatkowy
`manifest.json` dla Docker load. Zawiera niezmienione skompresowane warstwy
wydawcy, bez rozpakowywania i ponownej kompresji. Współczesny loader Dockera
[rozpoznaje kompresję warstw](https://github.com/moby/moby/blob/v28.3.3/image/tarexport/load.go).
To weryfikacja integralności i pochodzenia przez HTTPS registry wydawcy;
nie zweryfikowano odrębnego podpisu producenta tych obrazów. APK i baza
Alpine zachowują istniejącą ścieżkę weryfikacji podpisów QSX.

Cały cache jest potrzebny do odtworzenia, także APK Dockera i zależności,
baza Alpine, Codex, jego dowody i archiwum Lean. Zwykły lock QSX obejmuje
inline kod przygotowania, usługę i wszystkie dodatkowe wejścia. Zmiana
któregokolwiek z nich wymaga nowego locka. Skrypt pobierania nie jest
nowym backendem kontrolera; receptura używa istniejącego `[[artifacts]]`.

Helper używa domyślnie `--connections 4`. Dla warstw co najmniej 32 MiB
zachowuje kompletne 16 MiB zakresy i ich sumy w `cache/transfers/SHA256/`.
Ponowne wywołanie z nowym katalogiem `--output` wykorzystuje te zakresy;
częściowe transfery i błędne sklejenia pozostają jako dowody. Wynikowy blob
musi przejść SHA-256 przypięty przez wydawcę. Anonimowy token jest odnawiany
przed każdym żądaniem i jeszcze raz po pojedynczym HTTP 401. Nie jest
zapisywany w częściowych plikach ani logach. Regresje sprawdzają wygaśnięcie
tokenu, wznowienie po przerwaniu strumienia i odrzucenie złego SHA całego wejścia.

W tej sesji pierwotny wolny transfer i pierwsza próba zakresowa zostały
zachowane; ta druga zakończyła się HTTP 401 po wygaśnięciu tokenu. Kontynuacja
sprawdziła prefiks oraz 51 kompletnych zakresów i pobrała tylko resztę. Końcowy
helper wykonał mieszany przebieg: 162801247 B świeżych, zweryfikowanych wejść
oraz 1306524274 B ponownie zweryfikowanych wejść z wcześniejszych pobrań.
Nie jest to test pobrania całego cache od zera przez końcową wersję helpera.
Jego rzeczywisty świeży transfer zakresowy objął 133253574 B warstwy Sage.
Archiwum OCI ma 1469347840 B, SHA-256
`b68aedc93f3bed8411476e252fedd92dc3f22a622504ea9a85dab730fb4fe76f`.
Czas końcowego mieszanego freeze i archiwizacji wyniósł 125.1 s.

## Offline build i prywatna instancja

Wskaż rzeczywistą ścieżkę wypisanego locka i nową nazwę obrazu:

```sh
qsx-alpine image-build /RZECZYWISTY/LOCK.json \
  --cache /TRWALY/KATALOG/qsx-input-cache \
  --output /TRWALY/KATALOG/sage-lean4-container-01.qcow2 \
  --timeout 3600
qsx-alpine image-verify /TRWALY/KATALOG/sage-lean4-container-01.qcow2.manifest.json
```

Builder nie ma sieci. Przygotowanie uruchamia OpenRC/cgroups i Dockera,
sprawdza wejścia, rozpakowuje Lean w gościu, strumieniuje archiwum do
`docker load` i sprawdza wynikowy identyfikator obrazu. Obraz i warstwy
pozostają danymi prywatnego dysku VM. Po restarcie Docker i niewielka usługa
`qsx-science` są uruchamiane przez OpenRC. Tworzenie instancji, import oraz
konsola używają zwykłych poleceń QSX; ich składnia jest w runbooku.

`agent` nie jest członkiem grupy docker i nie otrzymuje jej socketa.
Niewielka usługa root nasłuchuje wyłącznie w gościu na AF_UNIX pod
`/run/qsx-science/control.sock`. Sprawdza UID klienta i akceptuje dokładnie
`sage`, `lean` oraz `lake`, ich argumenty i cwd wewnątrz rzeczywistego
`/workspace/project`. Nie przyjmuje flag Dockera, obrazu, UID, konfiguracji
sieci ani ścieżek montowań. Kod i socket są kontrolowane przez root.

Kontener zachowuje UID/GID `agent` i otrzymuje jedną dodatkową grupę 1000
wyłącznie wewnątrz kontenera. Przypięty obraz wydawcy umieszcza Sage pod
`/home/sage` z trybem 0750 i UID/GID 1000; agent Alpine może mieć UID/GID 1001.
To pozwala czytać narzędzia bez zmiany właściciela wyników w workspace,
grup konta gościa lub hosta. Readiness porównuje UID/GID uruchomionego Sage
z rzeczywistym kontem agenta i sprawdza dodatkową grupę.
Kontener ma tylko dwa stałe bindy z dysku gościa: workspace
oraz Lean tylko do odczytu. Rootfs jest tylko do odczytu, `/tmp` jest tmpfs,
sieć ma `none`, wszystkie capabilities są usunięte i włączone jest
`no-new-privileges`. Obowiązuje limit 3 GiB pamięci, 4 CPU i 256 procesów,
600 sekund na wywołanie oraz 32 MiB wyjścia. Zadania są szeregowane, aby nie
mnożyć przydziału pamięci. Zakończenie lub timeout usuwa kontener. Nie ma
montowań katalogów hosta, hostowego socketa Docker, publikowanych portów
ani API sieciowego. Usługa jest elementem zaufanej receptury operatora.

Wrappery są obecnie nieinteraktywne: przekazują argumenty i pliki w workspace,
a stdin kontenera jest zamknięty. Działają `sage -c`, `sage -python`, `lean`
i `lake`; konsola QSX nadal jest zwykłą konsolą Alpine. Tryb REPL Sage ze
strumieniowym stdin nie jest obecnie implementowany. Nie zmieniono sandboxu
Codexa, aby dopuścić narzędzia; jego dostęp musi przejść rzeczywisty test.

Tożsamość silnika Dockera jest usuwana z budowanego wzorca przez
`/usr/local/libexec/qsx-science-seal.py`, wywoływany z `stop_post` w konfiguracji
OpenRC Dockera. [Kolejność OpenRC](https://github.com/OpenRC/openrc/blob/master/sh/openrc-run.sh.in)
wywołuje ten hook po pomyślnym zatrzymaniu usługi. Helper usuwa wyłącznie
`/var/lib/docker/engine-id` i tylko gdy nie istnieje `/var/lib/qsx-instance-id`.
Znacznik importu prywatnej instancji, także pusty lub symlink, powstrzymuje
usuwanie. Warstwy i identyfikator przypiętego obrazu pozostają zachowane.
Normalne restarty prywatnej instancji zachowują jej własną tożsamość silnika.

## Odbiór i dowody

Readiness wykonywane jako nie-root `agent` sprawdza:

- dokładny wyznacznik macierzy nad ZZ oraz potęgowanie w GF(7);
- zgodność UID/GID Sage z kontem agenta i dodatkową grupę 1000 kontenera;
- kompilację `example (n : Nat) : n + 0 = n := rfl` do niepustego `.olean`;
- utworzenie projektu Lake, `lake build` i uruchomienie otrzymanego programu;
- to samo narzędzie Sage wywołane przez przypięty sandbox Codexa, z dokładną
  arytmetyką i sprawdzeniem niezerowego UID.

`QSX_READY` bez przejścia wszystkich kroków nie zostanie ogłoszone. Test
sandboxu nie wymaga modelu ani loginu; osobiste logowanie i rzeczywiste
zadanie modelu są osobnym odbiorem. Wzorzec nie ma danych projektu ani
poświadczeń. Testy syntetyczne usuwają własny katalog w workspace.

Po rzeczywistym shutdown buildera sprawdź w `.boot.log` znacznik
`QSX_SCIENCE_TEMPLATE_IDENTITY_CLEARED` i brak `/var/lib/docker/engine-id`
na nieaktywnym wzorcu. Sam wcześniejszy `QSX_READY` nie sprawdza hooka
wykonywanego podczas zamykania. W dwóch prywatnych instancjach odczytaj
`docker info --format '{{.ID}}'` przez administracyjną konsolę gościa,
sprawdź różne wartości i ich zachowanie po restarcie. Tych czynności nie
zastępuje test helpera na plikach syntetycznych.

Historycznie własne testy w `tests/test_sage_container.py` dały 13 PASS
(kontrakt argumentów, ucieczki ścieżek, zakaz root, graf OCI, składnia
skryptów, odnowienie tokenu i wznawianie transferu); Ruff helpera i testów PASS.
Freeze wejść zakończył się PASS. Na tamtym etapie budowa, boot KVM i runtime
narzędzi miały `NOT_RUN`; operator wstrzymał nowe budowy do przekazania szablonu
AGENTS dla VM. Aktualna instrukcja operatora zleca ich kontynuację.
Prebuild review wykazało, że `/home/sage` w przypiętym obrazie ma 0750,
UID/GID 1000. Późniejsza poprawka receptury dodaje opisaną wyżej grupę tylko
w kontenerze i hook usunięcia tożsamości silnika wzorca. Nowe regresje obejmują
UID/GID 1001 wszystkich trzech narzędzi, usunięcie wyłącznie engine-id ze wzorca,
zachowanie tożsamości instancji, symlinki i zgłoszenie błędu usunięcia.
Przed poprawką trzy przypadki UID/GID 1001 nie przechodziły; po poprawce cały
plik ma 22 PASS. Po zmianie receptury potrzebny jest nowy lock; przypięte
wejścia pozostają te same. Nie traktuj tej notatki ani testów hostowych jako
raportu udanego builda.

### Rzeczywista próba KVM na Ubuntu, 2026-09-08

Host: Ubuntu 24.04.4 LTS, x86_64, kernel `6.8.0-139-generic`, Python 3.12.3.
Próba używała zainstalowanego runtime z commitu
`08c30182c1c08f51b15d4320a8fc86267077d107` oraz trzech ówczesnych zmian
roboczych: tej dokumentacji, receptury i `tests/test_sage_container.py`.
Ich dokładny zakres zachowano w `build-source.patch`; poprawki receptury
zostały następnie zapisane w `ab19860d3b6bc3e047e08d1247bf98e7a14bd7e2`.
SHA-256 użytej receptury:
`d5f8846e31f65bb8a181a61a38fa5ba08155224c972443d7807eabdc3887f6ed`.
Lock: `947bea03fcc23a33b86d52932ff8875e8b32dd341148ff0d579880e3d061bdb2`.

Freeze zakończył się `PASS` w 131.55 s. Build trwał 3027.33 s, od
`2026-09-08T06:03:41Z` do `2026-09-08T06:54:10Z`, i zakończył się kodem CLI 2.
Konfiguracja: KVM, 4096 MiB RAM, 4 vCPU, dysk 32 GiB, timeout gościa 3600 s,
budżet konsoli 64 MiB, domyślna niezmieniona polityka. Gość nie miał NIC;
kontroler działał jako zwykły operator. Nie zmieniano usług, ACL ani sieci hosta.
Łączny czas obejmuje przygotowanie ISO i cały przebieg; nie jest pomiarem
samego startu. Zaobserwowane wysokie obciążenie I/O na dysku USB ograniczało
przepustowość; przeniesienie na inny nośnik wymaga nowego pomiaru.

| Sprawdzenie w rzeczywistym gościu | Status | Dowód i granica wyniku |
|---|---|---|
| Alpine uruchomione przez KVM, Docker i przypięte warstwy załadowane offline | `PASS` | Konsola rzeczywistej VM; to etap przygotowania, nie pełny odbiór wzorca. |
| Sage: dokładna arytmetyka oraz zgodność UID/GID agenta | `PASS` | `SAGE_EXACT_PASS`. |
| Lean: niepusty `Exact.olean`; Lake: budowa i wynik programu | `PASS` | `LEAN_PROOF_AND_LAKE_BUILD_PASS`, budowa Lake zakończyła 8 zadań. |
| Wywołanie Sage przez sandbox Codexa 0.153.4 | `FAIL` | `connect()` do `/run/qsx-science/control.sock` zwróciło `EPERM`; brak `CODEX_SANDBOX_SAGE_PASS`. |
| Gotowy obraz Sage, manifest i komplet readiness | `FAIL` | `QSX_BUILD_FAILED`; builder poprawnie odmówił publikacji. |
| Usunięcie tożsamości Dockera ze wzorca i różne ID dwóch instancji | `NOT_RUN` | Wymaga udanego wzorca, kontroli po shutdown i dwóch prywatnych instancji. |
| Zalogowane zadanie modelu | `NOT_RUN` | Transport, jego odbiór i osobiste logowanie pozostają osobnym etapem. |

Lake zgłosił brak programu `git` podczas inicjalizacji repozytorium wewnątrz
kontenera. Późniejszy `lake build` i uruchomienie programu powiodły się;
ostrzeżenia nie należy usuwać z dowodów ani uznawać za działającą obsługę Git
wewnątrz tego kontenera. Program Git zainstalowany w Alpine jest osobnym runtime.

Po błędzie gość wykonał poweroff, a proces QEMU zakończył się kodem 0.
Ten kod opisuje zakończenie emulatora; nie zmienia wyniku całej budowy `FAIL`.
Nie ustalono jeszcze dokładnej przyczyny `EPERM` w warstwie sandboxu.
Sam odnotowany błąd nie rozstrzyga, czy wynika z seccomp, przestrzeni nazw
czy innego mechanizmu. Zmiana hosta na Debian nie jest sprawdzoną poprawką.
Następny krok to mała reprodukcja w gościu i naprawa integracji przy zachowaniu
polityki `workspace-write`, braku sieci narzędzi i kontroli dostępu brokera.

Trwałe dowody leżą poza repo w katalogu roboczym operatora:

- `qsx-local-sage-20260908-001/build-attempt.json`: dokładne argv, commit,
  zmiany robocze, zasoby, czasy i wynik;
- `qsx-local-sage-20260908-001/build-source.patch`: stan receptury przy starcie;
- `qsx-local-sage-20260908-001/guest-console.log`: wynik narzędzi i błąd sandboxu;
- `qsx-local-sage-20260908-001/image-build.log`: odmowa publikacji i ścieżka
  zachowanego katalogu nieudanej budowy;
- `qsx-local-sage-20260908-001/freeze-attempt.json`: pomiar freeze i jego zakres.

Katalog bazowy na Ubuntu to
`/home/god/Muzyka/QSX-ALPINE-SANBOX-v1`. Zachowany roboczy dysk nieudanej
budowy nie jest wzorcem dostawy i nie ma zaakceptowanego manifestu.
Późniejsze poprawki kolektora logów i przygotowania ISO nie były częścią
runtime tej próby. Zmiana tych helperów wymaga nowego runtime i nowego locka;
stary lock oraz dokładny runtime pozostają dowodem historycznym.
Na polecenie operatora następne uruchomienia przechodzą na Debian 13 zgodnie
z [instrukcją następnego etapu](DEBIAN_NEXT_STAGE_PL.md).
