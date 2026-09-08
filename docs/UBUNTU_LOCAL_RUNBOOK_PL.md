# Ubuntu 24.04 — bieżący warsztat i prywatny storage libvirt

Stan: 2026-09-07. Host faktycznie rozpoznany jako **Ubuntu 24.04.4 LTS x86_64**,
kernel `6.8.0-139-generic`, Python 3.12, QEMU 8.2.2 z pakietu Ubuntu
`1:8.2.2+ds-0ubuntu1.18`. Gość: Alpine 3.24.1. Host ma około 15 GiB RAM,
3.9 GiB swap i 219 GiB wolnego miejsca według zapisanego preflightu; to migawka,
nie limit zasobów. Fakty użytkownika: `god` UID/GID 1002, konto QEMU
`libvirt-qemu` UID 64055/GID 994. Dostęp operatora do `/dev/kvm` zapewnia istniejący
ACL. Odczyty źródłowe: `../../qsx-local-evidence-001/host.log`, `doctor.log`.

## Co już rzeczywiście wykonano

`scripts/demo_debian.py --run --prepare-only` zakończył się poprawnie na tym
Ubuntu. Zbudowano przez KVM starter z profilem Codex, ponownie opublikowano go
z cache do nowej ścieżki oraz zbudowano nowy szablon z Pythonem. Wzorce są bez
logowania operatora. Wszystkie poniższe ścieżki są trwałe na tym hoście:

```text
/home/god/Muzyka/QSX-ALPINE-SANBOX-v1/qsx-local-demo-001/images/
/home/god/Muzyka/QSX-ALPINE-SANBOX-v1/qsx-local-demo-001/demo-state.json
/home/god/Muzyka/QSX-ALPINE-SANBOX-v1/qsx-local-demo-001/demo-report.json
/home/god/Muzyka/QSX-ALPINE-SANBOX-v1/qsx-local-evidence-001/demo-prepare-001.json
/home/god/Muzyka/QSX-ALPINE-SANBOX-v1/qsx-input-cache/
```

| Pomiar | Starter | Publikacja z cache | Nowy szablon Python |
|---|---:|---:|---:|
| Freeze, czas polecenia | 10.939 s | ten sam lock | 38.177 s |
| Czas polecenia build | 124.999 s | 1.278 s | 127.648 s |
| `build_seconds` | 121.963 s | zachowane z pierwszego builda | 125.906 s |
| `boot_seconds` build gościa | 77.432 s | bez nowego bootu | 97.248 s |
| `publish_seconds` | 0.287 s | 0.277 s | 0.143 s |
| Plik / zajęte bloki | 250806272 B | 250806272 B | 121307136 B |
| Rozmiar wirtualny | 8 GiB | 8 GiB | 8 GiB |
| Konfiguracja budowy | 1024 MiB, 1 vCPU | bez nowej VM | 2048 MiB, 2 vCPU |

Źródłem są aktualne manifesty `.qcow2.manifest.json` i kroki `demo-report.json`.
`boot_seconds` oznacza obserwację fazy budowy, nie zmierzony późniejszy start
prywatnej instancji. RAM/vCPU w tabeli to konfiguracja; rzeczywiste zużycie RAM
nie zostało tu zmierzone. Zajęte bloki podano w bajtach według manifestów.

SHA256 startera i publikacji z cache:
`7ff0226ad65ed0cab1a7a54c08e9c22c917d23bf585c06a4969f62fb3e32f43a`.
SHA256 wzorca Python:
`57b65c0974efdb695c6cd335af738aa9afd8706bb2dac152526bc4e9a914dafc`.
Cache ma `cache_hit=true`: jest ponowną publikacją zachowanego wyniku, nie dwiema
niezależnymi kompilacjami. Każdy wzorzec ma manifest i `.boot.log` obok QCOW2.

Systemowy libvirt pozostaje nieuruchomiony; początkowe prywatne instancje mają
`domain_defined=false`. Ich odbiór konsoli/importu/izolacji/ACPI jest **NOT_RUN**.
Debian 13 również **NOT_RUN**. Starter build przez KVM nie zastępuje tych testów.
Nowe instancje są obecnie wstrzymane do uwzględnienia szablonu `AGENTS.md`, który
operator zapowiedział dla każdej VM. Sage/Lean i zalogowane zadanie modelu mają
oddzielne procedury i dowody; ta notatka ich nie zalicza.

## Przygotowana zmiana storage — jeszcze niewykonana

Nie nadajemy QEMU przejścia ani odczytu do `/home/god`. Docelowy stan prywatnych
instancji: `/var/lib/libvirt/images/qsx-alpine-local-001`, początkowo `god:god 0700`.
Istniejący przodek `/var/lib/libvirt/images` ma 0711 i już pozwala przechodzić;
nie wymaga zmiany jego uprawnień. Poniższy skrypt jest konkretnym zakresem do
zatwierdzenia, a nie otrzymaną zgodą. Wykonywać jako `god`, nigdy `sudo python`.

```sh
cd /home/god/Muzyka/QSX-ALPINE-SANBOX-v1/qsx-delivery/qsx-alpine
.venv/bin/python scripts/transition_demo_storage.py --plan
```

Plan wymienia wszystkie uprzywilejowane polecenia oraz dokładnie dwie pary:

| Dotychczasowa instancja pod `qsx-local-demo-001/instances` | Nowy katalog |
|---|---|
| `demo-a-ecbc2e1f` | `/var/lib/libvirt/images/qsx-alpine-local-001/demo-a-ecbc2e1f` |
| `demo-b-ecbc2e1f` | `/var/lib/libvirt/images/qsx-alpine-local-001/demo-b-ecbc2e1f` |

Tworzenie wykona zwykłe zainstalowane CLI
`../../qsx-local-tools/runtime-3d6e4dc/bin/qsx-alpine` z zachowanego wzorca i importu.
Każda instancja ma żądanie 1024 MiB / 1 vCPU / 8 GiB i osobną nową tożsamość.
Stare instancje, wzorce i wyniki pozostaną na miejscu. Nowe nazwy domen są takie
same, dlatego przed utworzeniem skrypt wymaga, aby żadna z tych domen nie była
zdefiniowana w systemowym libvirt.

Istotny fakt hosta: istnieje symlink
`/etc/libvirt/qemu/networks/autostart/default.xml` do
`/etc/libvirt/qemu/networks/default.xml`. Zawartość docelowej konfiguracji jest
niedostępna dla `god`; nie uznano więc jej faktycznego trybu sieci za sprawdzony.
Samo uruchomienie daemonu mogłoby aktywować tę sieć. **Nie zatwierdzono jej startu**.
Przejrzany dodatkowy krok przenosi tylko ten symlink, bez nadpisania, do
`/var/lib/libvirt/qsx-alpine-local-001-autostart-backup/default.xml` w katalogu
`root:root 0700`. Zachowuje target, właściciela, tryb i mtime w dzienniku oraz
weryfikuje je po przeniesieniu. Inny autostart sieci lub domeny zatrzymuje skrypt.
Dopiero wtedy `sudo -n systemctl start libvirtd.socket libvirtd.service`, bez
`enable`. Odczyt `virsh net-list --name` musi potwierdzić brak aktywnych sieci.

Po zatwierdzeniu tego konkretnego zakresu, zakończeniu uzgodnienia `AGENTS.md`
i udostępnieniu przez operatora uwierzytelnionego sudo można wykonać:

```sh
.venv/bin/python scripts/transition_demo_storage.py --apply
```

Jedynymi zmianami root są przygotowanie backupu i przeniesienie symlinku,
uruchomienie wskazanych usług oraz `install -d -o god -g god -m 0700` docelowego
katalogu. Nie ma zmian grup, pakietów, AppArmor ani reguł sieci. Odczyt backupu
korzysta z wąskich `sudo readlink/stat`. Obecnie `sudo -n` wymaga hasła, dlatego
etap wykonawczy jest **BLOCKED**, a skrypt ma wyłącznie wykonane `--plan` i testy.

ACL ustawia już sam właściciel `god`: `u:64055:--x` na docelowym katalogu i
katalogu danej instancji, `u:64055:rw-` tylko na `disk.qcow2`, `u:64055:r--` tylko
na `import.iso`. Brak rekurencji, wildcardów, domyślnych ACL i odczytu JSON.
Maska extended ACL jest widoczna w polu grupowym `stat`; grupa właściciela nadal
ma `group::---`. Skrypt sprawdza dokładne ACL przed i po, odmawiając poszerzania
istniejących obcych ACL. Sprawdza też brak backing file i rozmiar dysku.

Dopiero po powodzeniu obu nowych instancji i wszystkich odczytów ACL skrypt
zachowuje dokładne kopie `demo-state.json` i `demo-report.json` w
`../../qsx-local-evidence-001/storage-transition-original-*`, dopisuje raport
storage i atomowo przełącza listę instancji. Logi poleceń i dziennik przejścia
trafiają do tego samego katalogu dowodów. Błąd lub timeout zachowuje artefakty;
niepowiązany z dziennikiem istniejący katalog wymaga przeglądu, nie nadpisania.

Po zakończonym przejściu rzeczywisty odbiór uruchamia się osobno:

```sh
.venv/bin/python scripts/demo_debian.py --continue \
  --work-dir /home/god/Muzyka/QSX-ALPINE-SANBOX-v1/qsx-local-demo-001 \
  --qsx /home/god/Muzyka/QSX-ALPINE-SANBOX-v1/qsx-local-tools/runtime-3d6e4dc/bin/qsx-alpine
```

Reguła ACL może później objąć jawnie wskazaną instancję Codex/Sage albo nowy
nośnik `network-prepare`, bez tworzenia ich teraz:

```sh
.venv/bin/python scripts/transition_demo_storage.py --grant-storage \
  /var/lib/libvirt/images/qsx-alpine-local-001/NAZWA/instance.json
```

Ta komenda nie używa sudo. Akceptuje tylko bezpośredni katalog instancji pod
wskazanym rootem, stan undefined/SHUTOFF, zwykłe pliki właściciela oraz bieżący
`egress-NONCE.iso` z poprawnym hashem w metadanych. Nie przyznaje dostępu do
wszystkich ISO w katalogu. Dalsze instancje pozostają do osobnego wykonania.

## Przywrócenie poprzedniego autostartu — tylko po nowej decyzji

Przeniesienie symlinku jest odwracalne. Przywrócenie może w przyszłości uruchomić
sieć `default`, więc nie następuje automatycznie po demo ani po błędzie:

```sh
sudo readlink /var/lib/libvirt/qsx-alpine-local-001-autostart-backup/default.xml
sudo mv --no-clobber -T -- \
  /var/lib/libvirt/qsx-alpine-local-001-autostart-backup/default.xml \
  /etc/libvirt/qemu/networks/autostart/default.xml
```

Wcześniej sprawdzić brak symlinku docelowego i brak niezamierzonego nadania sieci.
Nie usuwać wyników/backupów. Powrót listy instancji do starego stanu także wymaga
sprawdzenia rzeczywistego libvirt: tych samych nazw nie wolno uruchamiać z dwóch
różnych katalogów. Samo odtworzenie starego JSON nie zatrzymuje działającej VM.

## Narzędzia i powtarzanie testów

Na tym Ubuntu użyto Pythona 3.12 i `requirements-dev.lock` z dostarczonym
wheelhouse; lock 3.13 jest dla osobnego środowiska, nie zmieniamy systemowego
Pythona. Bieżące wyniki `make check` mają numerowane logi w
`../../qsx-local-evidence-001/`; wyniki 115 testów z dostawy są historyczne.
Oddzielny runtime powstał z aktualnego wheela; nie odświeżać działającego venv
freeze/build w połowie jego operacji. Po zmianie kodu najpierw zakończyć aktywne
operacje, wykonać `make check` i zainstalować aktualny wheel do nowego runtime.
Nowy kod objęty lockiem wymaga nowego freeze; wcześniejsze locki/dowody zachować.

Skrypt storage: 16 testów kontraktowych PASS oraz Ruff PASS. Testy używają plików
tymczasowych i syntetycznego CLI; nie zaliczają ACL QEMU, startu libvirt ani VM.
Procedura egress i logowania jest oddzielna: `CODEX_EGRESS_PROPOSAL_PL.md`.
