# Następny etap: odbiór QSX na Debianie 13

Na polecenie operatora z 2026-09-08 kończymy bieżący etap na Ubuntu;
następne uruchomienia VM wykonujemy po instalacji Debiana. Gość pozostaje
Alpine. To przekazanie niezakończonego odbioru, nie deklaracja ukończenia QSX.
Panel rozwija operator osobno; nie jest warunkiem rozpoczęcia testów CLI.

## Stan przekazania

Rzeczywisty checkout na dotychczasowym hoście:
`/home/god/Muzyka/QSX-ALPINE-SANBOX-v1/qsx-delivery/qsx-alpine`.
Gałąź: `feat/alpine-task-images`. Remote do późniejszego push:
`https://github.com/niirmataa/qsx-sandbox.git`.
Kod wykonawczy zamkniętego etapu to
`e59cb4151d701ebe78694940ff0132127550ea71`. Późniejszy commit dokumentacji,
dokładny końcowy HEAD, stan drzewa, bundle i jego sumę podaje raport zamknięcia
etapu w `qsx-local-debian-handoff-20260908` poza repo. Nie należy odtwarzać
końcowego HEAD z nazwy starej paczki albo cofać do
dostarczonego `fbaeca1686e4829bf68f68e9d2a4862bce7e6576`.

Dotychczasowy host: Ubuntu 24.04.4 LTS, x86_64, kernel `6.8.0-139-generic`,
Python 3.12.3. Wyniki poniżej dotyczą tego środowiska. **Wszystkie rzeczywiste
testy na Debianie 13 mają obecnie `NOT_RUN`.**

| Zakres na Ubuntu | Wynik i dowód |
|---|---|
| Ostatnie `make check` | `PASS`: 317 testów, Ruff, mypy 17 modułów, wheel i instalacja poza źródłami; kod wyjścia 0, 53.15 s. `qsx-local-evidence-20260908/make-check-seed-mapping-host.json` i `.log` zapisują HEAD `ab19860d3b6bc3e047e08d1247bf98e7a14bd7e2` oraz trzy testowane zmiany następnie zapisane w `e59cb41`. |
| Wcześniejsze obrazy starter/task | Zachowane rzeczywiste buildy KVM z manifestami w `qsx-local-demo-001/images/`; ich własne manifesty określają użyte locki i zakres. Nie są buildem najnowszego helpera. |
| Odczyt nieaktywnego `task.qcow2` | `PASS`: ext4 zajmuje cały wirtualny dysk 8 GiB, brak tabeli partycji, filesystem clean, hash obrazu niezmieniony; sześć nazwanych ścieżek poświadczeń/tożsamości nieobecnych. Dowód: `qsx-local-sage-20260908-001/inspection-existing-task-03/result.json`. Nie jest to odbiór bootu ani dowód braku dowolnych sekretów poza tymi ścieżkami. |
| Sage + Lean, build KVM offline | `FAIL`, kod CLI 2, 3027.33 s. Sage dokładnie liczy; Lean tworzy `.olean`, Lake buduje i uruchamia program jako nie-root. Wywołanie Sage z sandboxu Codexa kończy się `EPERM` przy połączeniu z brokerem AF_UNIX. Brak zaakceptowanego obrazu Sage. Szczegóły w [raporcie warsztatu](SAGE_CONTAINER_WORKSHOP_PL.md). |
| Dwie prywatne VM, import/finalize, konsola, izolacja i ACPI | `NOT_RUN` jako pełny odbiór. Przygotowane instancje Ubuntu nie zostały uruchomione po zmianie storage. |
| Hostowe wdrożenie transportu wyjścia i testy allow/deny/cut | `BLOCKED`: konkretny zakres wymaga decyzji operatora; przygotowany kod i propozycja nie są wdrożeniem. |
| Zalogowane małe zadanie modelu | `NOT_RUN`; transport i osobiste logowanie są poprzedzającymi krokami. Nie jest jeszcze gotową sesją czekającą wyłącznie na hasło. |

Codex Security był dostępny i zakończył skany. Między innymi skan różnicowy
`37c2cac8-88b2-457d-8767-fe04472d9710` dla `78b8a55..08c3018` zakończył się
bez zgłoszeń po poprawkach, podobnie
`be321770-9703-4cee-8b4c-e385d3ee0aec` dla `08c3018..ab19860` (5 powierzchni).
Każdy wynik dotyczy zapisanego zakresu; nie potwierdza uruchomienia VM ani
braku błędu integracji Sage. Raport zamknięcia etapu wskazuje także wynik
osobnego skanu późniejszej zmiany przygotowania ISO. Zachowaj katalogi
`qsx-local-evidence-20260908/codex-security-*` z ich `SHA256SUMS`.

## Co zachować przed przeniesieniem

Zachowaj całe istniejące repo z `.git`, nowszymi zmianami operatora i wszystkimi
refami oraz końcowy bundle. Nie pracuj wyłącznie w `qsx-delivery/source/`.
Jeżeli checkout na Debianie już istnieje, zacznij od odczytu jego stanu;
nie nadpisuj go ponownym klonowaniem.

Poza repo potrzebne są:

- kompletny `qsx-input-cache`, w tym locki, bloby, APK i dowody pochodzenia;
  zachowane katalogi nieudanych budów są dowodami, nie gotowymi wzorcami;
- `qsx-delivery/qsx-dev-wheelhouse`, w szczególności `cp313` i jego sumy;
- `qsx-local-demo-001` z obrazami, manifestami, logami i stanem demonstracji;
- `qsx-local-sage-20260908-001`, `qsx-local-evidence-20260908` oraz
  `qsx-local-evidence-20260908-host`;
- źródłowe archiwa/przekazanie i wyniki freeze Sage, których zakres wskaże
  końcowy indeks cache.

Zweryfikuj końcowe sumy po skopiowaniu. Historyczny runtime Ubuntu zachowaj
jako dowód wersji, ale na Debianie utwórz nowy venv. Nie kopiuj do nowego
runtime Ubuntu `.venv`, hostowych poświadczeń ani zalogowanych instancji
jako wzorców. Kopiowanie katalogów nie potwierdza przenośności zapisanych
absolutnych ścieżek instancji; dla nowego hosta przygotuj nowe instancje.

Jeśli brakuje checkoutu, odtwórz mirror ze **zweryfikowanego końcowego bundle**,
a następnie sklonuj z niego gałąź roboczą według `START_HERE_PL.md`.
Mirror zachowuje komplet dostarczonych refów. W nowym checkoutcie remote
lokalnego mirrora zachowaj jako `delivery`, a `origin` ustaw na adres GitHub
podany wyżej. Ta instrukcja nie udziela nowej zgody na push, merge ani zmiany PR.

## Pierwsze polecenia na Debianie

Przejdź do faktycznego checkoutu. Polecenia poniżej odczytują stan; nie
uruchamiają usług libvirt i nie zmieniają pakietów ani uprawnień:

```sh
pwd
git status --short --branch
git branch --show-current
git rev-parse HEAD HEAD^{tree}
git remote -v
git fsck --full
cat /etc/os-release
uname -m
uname -r
python3 --version
lscpu
free -h
df -h .
ls -l /dev/kvm
id
getent passwd libvirt-qemu
systemctl is-active libvirtd.service libvirtd.socket virtqemud.service virtqemud.socket
cat /sys/module/apparmor/parameters/enabled
```

Zapisz wyniki i kody wyjścia. Brak jednej nazwy usługi może oznaczać inny
układ libvirt; ustal faktyczny stan. Sam odczyt `/dev/kvm` albo `doctor`
nie jest testem wykonania KVM. Nie przenoś wyników ograniczonego sandboxu
narzędzia na cały host bez rozpoznania jego kontekstu.

Przed nowymi zmianami uprzywilejowanymi przygotuj zakres dla faktycznego
Debiana zgodnie z [runbookiem](DEBIAN_RUNBOOK_PL.md). Do odczytu planu
pakietów można użyć:

```sh
apt-get --simulate install qemu-system-x86 qemu-utils libvirt-clients \
  libvirt-daemon-system xorriso python3-venv git gpg acl make
```

Cosign pozostaje przypięty do sprawdzonej wersji 3.1.3; procedura weryfikacji
pochodzenia jest w runbooku. Nie instaluj hostowego Dockera na potrzeby Sage.
Usługi, grupy, ACL i sieć wymagają przeglądu konkretnego wpływu i wcześniej
udzielonej zgody. Propozycja Ubuntu `HOST_CHANGE_PROPOSAL_08c3018_PL.md`
nie została wykonana ani zatwierdzona. Nie przenoś z niej UID operatora 1002,
UID QEMU 64055, ścieżek storage, autostartu sieci ani ustawień AppArmor.

## Nowy venv, testy i zainstalowany runtime

Poniższe polecenia zakładają potwierdzony Python 3.13, nowy checkout bez
`.venv` i faktyczne ścieżki wstawione zamiast `/SCIEZKA/...`. Jeśli istnieje
stary venv, zachowaj go i przygotuj nowy checkout z historią do odbioru.
Nie zastępuj systemowego Pythona. Dla faktycznie używanego 3.12 obowiązuje
`requirements-dev.lock` oraz wheelhouse `cp312`.

```sh
QSX_WHEELHOUSE=/SCIEZKA/qsx-delivery/qsx-dev-wheelhouse
QSX_RUNTIME=/SCIEZKA/POZA_REPO/runtime-debian-001
QSX_CACHE=/SCIEZKA/POZA_REPO/qsx-input-cache
QSX_DEMO=/SCIEZKA/POZA_REPO/qsx-debian-demo-001
python3.13 -m venv .venv
.venv/bin/python -m pip install --no-index --require-hashes \
  --find-links "$QSX_WHEELHOUSE/cp313" -r requirements-dev-py313.lock
make check
python3.13 -m venv "$QSX_RUNTIME"
"$QSX_RUNTIME/bin/python" -m pip install --no-index --no-deps \
  dist/qsx_alpine-0.1.0-py3-none-any.whl
"$QSX_RUNTIME/bin/qsx-alpine" doctor
.venv/bin/python scripts/demo_debian.py --help
.venv/bin/python scripts/demo_debian.py --plan \
  --cache "$QSX_CACHE" --work-dir "$QSX_DEMO" \
  --qsx "$QSX_RUNTIME/bin/qsx-alpine"
```

Wybierz świeże, nieistniejące katalogi runtime i demonstracji; cache zachowaj.
Przerwij sekwencję na błędzie i zapisz go. `doctor` zwraca dane JSON, więc
oceń statusy narzędzi, a nie sam kod wyjścia. Po każdej kolejnej zmianie kodu
odśwież testowany wheel/runtime i zapisz ich SHA wraz z HEAD i diffem.
Składnię `--help`, `--plan`, `doctor`, `template-lock` i `image-build`
sprawdzono na kodzie etapu Ubuntu; wykonanie na Debianie pozostaje `NOT_RUN`.

## Kolejność dalszego odbioru

1. Po preflight i `make check` odtwórz w małym gościu syntetyczny przypadek:
   nie-root wywołuje usługę narzędzi bezpośrednio, a następnie przez przypięty
   sandbox Codexa. Zapisz rozstrzygającą diagnostykę `EPERM`; opracuj poprawkę
   integracji z regresją oraz ponownym skanem. Nie wyłączaj seccomp/AppArmor,
   nie zmieniaj `workspace-write` na pełny dostęp, nie udostępniaj agentowi
   socketa Docker ani nie zwiększaj dostępu do sieci w celu przejścia testu.
2. Dla nowej implementacji wykonaj świeże `template-lock`. Poprawki kolektora
   logów oraz przygotowania ISO zmieniły kod objęty lockiem; stary lock
   `947bea03...` nie służy do budowy nowym runtime. Zachowaj stare wejścia
   i dowody; brak wejścia lub błąd podpisu naprawiaj wyłącznie podczas freeze.
3. Uruchom demo nowego hosta poniższym poleceniem. Po `--prepare-only`
   przejrzyj faktyczne ścieżki i konta storage, przygotuj potrzebne wąskie
   uprawnienia i dopiero w zatwierdzonym zakresie wykonaj `--continue`.

```sh
.venv/bin/python scripts/demo_debian.py --run --prepare-only \
  --cache "$QSX_CACHE" --work-dir "$QSX_DEMO" \
  --qsx "$QSX_RUNTIME/bin/qsx-alpine"
```

Po sprawdzeniu przygotowanego stanu i zapewnieniu zatwierdzonego dostępu
do storage tego przebiegu:

```sh
.venv/bin/python scripts/demo_debian.py --continue --work-dir "$QSX_DEMO" \
  --qsx "$QSX_RUNTIME/bin/qsx-alpine"
```

4. Po poprawce sandboxu zbuduj osobny warsztat Sage z nowego locka zgodnie
   z [jego instrukcją](SAGE_CONTAINER_WORKSHOP_PL.md). Dobierz jawnie zasoby
   do zmierzonego hosta i polityki; punkt próby Ubuntu to 4096 MiB, 4 vCPU,
   32 GiB i timeout 3600 s. Wymagaj wszystkich markerów gotowości, poprawnego
   shutdown i weryfikacji opublikowanego manifestu. Sprawdź brak tożsamości
   silnika we wzorcu i różne trwałe ID dwóch prywatnych instancji.
5. Uzupełnij demo o dwie równocześnie działające instancje, izolację zapisów,
   import i odłączenie nośnika po `finalize-import`, nie-root i workspace,
   faktyczny układ dysku/filesystemu, konsolę oraz ACPI. Zachowaj hash wzorca
   przed i po. Testy kontraktowe i inspekcja nieaktywnego dysku są osobnymi
   wynikami; nie zastępują tych obserwacji gościa.
6. Dostosuj [konkretną propozycję transportu](CODEX_EGRESS_PROPOSAL_PL.md)
   do nowego hosta i uzyskaj brakującą decyzję o zakresie wdrożenia. Przygotowane
   `network-prepare`/`network-grant` wymagają odbioru VM. Po wdrożeniu sprawdź
   allow/deny oraz odcięcie istniejącego połączenia i działającą konsolę po
   `network-cut`. VM od początku offline nie zalicza tego ostatniego testu.
7. Dopiero po odbiorze transportu operator osobiście loguje klienta we własnej
   prywatnej instancji. Wykonaj małe rzeczywiste zadanie modelu; zapisz wynik
   bez tokenów i bez surowego zapisu procesu logowania. Sprawdzenie sandboxu
   i `--version` nie zastępują zadania modelu.

Każdy krok zapisuje status, dokładne argv, commit i niezacommitowany zakres,
system/kernel, czas, kod wyjścia oraz ścieżkę logu. Dla builda zachowaj lock,
manifest, log konsoli, rozmiar wirtualny/pliku/zajętych bloków i skonfigurowane
zasoby; rzeczywiste zużycie opisuj oddzielnie. Po błędzie zachowaj artefakty
i sprawdź stan VM przed wznowieniem. Wynik skryptu
`PASS_LOCAL_DEMO_WITH_EXPLICIT_EXCLUSIONS` nadal wymaga jawnych wyłączeń.
