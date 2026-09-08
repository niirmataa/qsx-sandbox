# Receptura zadania + profil agenta

Samodzielny `qsx-alpine` buduje prywatne warsztaty z dwóch zwykłych plików TOML.
`starter`, `python-tests` i `c-build` są przykładami, a nazwy profili nie są
zakodowanymi rolami. Nowe pakiety i inny program agenta nie wymagają zmiany
kontrolera. Osobny kandydat na bazę badawczą SageMath + Lean 4 jest opisany
w `docs/SAGE_LEAN_TEMPLATE_PL.md`; jego budowa i działanie wymagają lokalnego
sprawdzenia. Te duże narzędzia nie są zależnością każdego szablonu.

```toml
# templates/my-experiment.toml
schema_version = 1
name = "my-experiment"
extends = "starter.toml"
agent_profile = "../agent-profiles/python-shell.toml"
packages = ["py3-pytest"]
prepare = ["mkdir -p /opt/experiment"]
readiness = ["python3 -m pytest --version"]

[resources]
memory_mib = 2048
vcpus = 2
disk_gib = 8

[[files]]
path = "/etc/experiment.conf"
content = "seed=42\n"
mode = "0644"
```

`extends` wskazuje jeden względny plik receptury. Maksymalna głębokość wynosi 16;
cykle są błędem. Profil agenta musi być wybrany jawnie w recepturze albo jej
wspólnej bazie; jego ścieżka jest względna wobec pliku, który ją deklaruje.

```toml
# agent-profiles/my-agent.toml
schema_version = 1
name = "my-agent"
packages = ["python3"]
argv = ["python3", "-q"]
readiness = ["python3 --version"]
```

Profil opisuje pakiety, wektor argumentów programu oraz opcjonalnie konfigurację
przypiętego Codexa, pliki, przygotowanie i testy gotowości. `codex.toml` zawiera
oficjalny Codex musl 0.153.4 z klientowym sandboxem `workspace-write`.
`python-shell.toml` uruchamia Pythona bez Codexa. Profil nie przyznaje zasobów,
sieci, montowań ani uprawnień hosta. `argv` jest uruchamiane wyłącznie jako
nieuprzywilejowany `agent` wewnątrz gościa, w `/workspace/project`.

Pakiety oraz kroki przygotowania i gotowości profilu są łączone z recepturą.
Zasoby zadania nadpisują odpowiednie pola odziedziczone. Plik o tej samej ścieżce
zastępuje konfigurację wspólnej bazy lub profilu. `template-check` pokazuje cały
wynik i hashe efektywnej receptury oraz profilu. Pakiet może być podany jako
`nazwa=wersja`; `template-lock` zapisuje rzeczywiste wersje i pełne zależności.

Przygotowanie wykonuje się jako root **wewnątrz jednorazowego gościa**; testy
jako `agent`. To kod zatwierdzany przez operatora, nigdy komenda hostowej powłoki.
Nie umieszczaj w obrazie projektu badawczego, poświadczeń ani tożsamości instancji.

Zasoby są żądaniem. Oddzielna polityka operatora ogranicza zarówno builder,
jak i runner. Domyślne maksima to 4096 MiB RAM, 4 CPU i 64 GiB dysku. Własny
plik polityki może zawierać `schema_version = 1` oraz `[limits]` z polami
`memory_mib`, `vcpus`, `disk_gib`. Przekroczenie jest błędem; nie ma cichego
zmniejszania zasobów. Budowa gościa zawsze odbywa się bez interfejsu IP.

## Komendy

Z katalogu repo po instalacji opisanej w głównym README:

```sh
qsx-alpine template-list templates
qsx-alpine template-check templates/starter.toml
qsx-alpine template-new templates/my-experiment.toml --name my-experiment --agent-profile agent-profiles/python-shell.toml --package py3-pytest
qsx-alpine template-lock templates/starter.toml --cache ./artifacts/input-cache
```

Ostatnia komenda podaje dokładną ścieżkę lockfile. Budowę wykonaj lokalnie,
wstawiając ją zamiast `LOCK.json`:

```sh
qsx-alpine image-build LOCK.json --cache ./artifacts/input-cache --output ./artifacts/starter.qcow2
qsx-alpine image-verify ./artifacts/starter.qcow2.manifest.json
qsx-alpine image-build LOCK.json --cache ./artifacts/input-cache --output ./artifacts/starter-cached.qcow2
```

Powtórna publikacja używa nowej nazwy wyjścia. Poprzedni obraz nie jest
nadpisywany. Osobne `--policy operator.toml` na `image-build` wybiera politykę.
Domyślnie wymagany jest KVM; `--accelerator tcg` to jawny test bez akceleracji,
nie automatyczny fallback produkcyjny.

## Dodatkowe wejścia do budowy

Receptura i profil obsługują `[[artifacts]]` z dokładnie trzema polami: `name`,
`url` i `sha256`. Nazwa jest zwykłą nazwą pliku ASCII, bez ścieżki; URL wymaga
HTTPS, a SHA-256 przypina oczekiwane bajty. Własny wpis o tej samej nazwie
zastępuje cały wpis odziedziczony. Powtórzenie nazwy w jednym pliku jest błędem.

`template-lock` pobiera i weryfikuje taki plik do cache. Builder kopiuje jego
niezmienione bajty na nośnik tylko do odczytu:
`/mnt/qsx-build/artifacts/NAME`. Rozpakowanie i kompilację opisuje `prepare`
wykonywane wyłącznie w gościu. Kontroler nie rozpakowuje ani nie wykonuje tych
wejść. SHA-256 dodatkowego pliku jest kontrolą integralności; weryfikację
osobnego podpisu jego producenta należy opisać w recepturze, jeśli jest dostępny.
Dłuższa budowa może wymagać jawnego `image-build --timeout 43200` oraz większych
limitów w osobnej polityce operatora.

## Cache i dowody

Zachowaj **cały katalog cache**: bazę Alpine 3.24.1 BIOS/cloud-init, pakiet
Codexa, APK i ich pełne zależności, podpisane indeksy, klucze, podpisy oraz locki.
Same listy wersji nie wystarczą po usunięciu starych plików przez producenta.
Pobieranie odbywa się tylko podczas `template-lock`; brak wejścia podczas budowy
jest błędem. Alpine i jego runtime APK mają sprawdzane podpisy GPG, APK podpisy
Alpine, a trzy komponenty ELF Codexa podpisy Sigstore. Cały pakiet Codexa nie ma
opublikowanego podpisu zbiorczego; jest przypięty oficjalnym SHA-256 wydania.

Zmiana receptury, profilu lub kodu helpera tworzy nowy lock. Identyczne wejścia
bazowe i zestawy pakietów pozostają w cache i nie wymagają pobierania ponownie.
`--refresh` oznacza jawne ponowne rozwiązanie wersji APK; nie jest potrzebne po
samej zmianie kodu. Budowa sprawdza hashe i podpisy APK bez sieci; weryfikacja
Sigstore online należy do freeze, a jej materiały dowodowe są zachowane w locku.

Cache obrazu uwzględnia lock, helpery, wersje i hashe narzędzi oraz akcelerator.
Ponowne użycie wymaga poprawnego hasha obrazu i logu, znacznika rzeczywistej
gotowości i `qemu-img check`. Gotowy obraz jest samodzielnym QCOW2 bez backing
file ani zewnętrznego pliku danych. Przed uruchomieniem parsera QEMU kontroler
sprawdza ograniczony nagłówek i rozszerzenia QCOW2, odrzucając takie zależności;
następnie wymusza format i wykonuje `qemu-img check`. Obsługiwane pozostają
samodzielne obrazy v2/v3, także ze skompresowanymi danymi zlib lub zstd.
Wybrane obrazy muszą pozostawać niezmienione w storage kontrolowanym przez
operatora podczas weryfikacji i użycia. Zachowaj razem QCOW2, `.manifest.json`
oraz `.boot.log`.

Konsola buildera ma osobny hostowy budżet: domyślnie 64 MiB, jawnie ustawiany
przez operatora jako `image-build --log-limit-mib 128`. Przekroczenie limitu
kończy build błędem, zatrzymuje jego proces i zachowuje prefiks logu mieszczący
się w limicie. Nie jest zaliczoną budową z obciętym logiem. Zbyt duży log cache
również blokuje ponowną publikację przy wybranym limicie. Wyszukiwanie znacznika
gotowości i `image-verify` czytają log strumieniowo; manifest zachowuje jego hash
oraz budżet zastosowany podczas pierwotnego builda. Budżet nie zmienia limitów
RAM, CPU, dysku ani sieci gościa. Zmiana kodu walidatora/kolektora wymaga nowego
locka; poprzednie locki i dowody pozostają zachowane.

Manifest rozdziela czas budowy/startu, rozmiar wirtualny, długość pliku oraz
bloki zajęte na dysku. `cache_hit` oznacza użycie gotowego zweryfikowanego obrazu,
a nie nową inicjalizację od zera. Nie obiecujemy identyczności bajtowej dwóch
niezależnych inicjalizacji.

Test gotowości profilu Codex rzeczywiście uruchamia jego sandbox w gościu:
sprawdza zapis w workspace i odmowę zapisu poza nim, bez modelu i logowania.
Składnia pochodzi z [przypiętego wydania 0.153.4](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/cli/src/debug_sandbox.rs):
`codex -c 'sandbox_mode="workspace-write"' sandbox -- ...`.
W Work sprawdzono kod i wejścia; **budowa/start gościa, jego sandbox, KVM oraz
logowanie operatora pozostają NOT_RUN** do lokalnego odbioru na Debianie 13.
