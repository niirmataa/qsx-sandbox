# QSX: budowa w chmurze, odbiór na docelowym hoście

Wersja 1.1-cloud — 2026-09-05. Wymagania projektu; nie raport z wdrożenia.

## 1. Dwa różne środowiska

Codex Cloud jest miejscem tworzenia i sprawdzania oprogramowania QSX. Laptop
właściciela będzie miejscem jego późniejszego uruchomienia. Brak laptopa nie jest
powodem do oddania samej architektury. Jest powodem do ograniczenia deklaracji
zgodnie z faktycznymi dowodami.

Kontener runnera nie jest docelową VM MathResearch. Prywatny dysk 60 GiB to
wymaganie przyszłej VM; nie żądaj 60 GiB przydziału runnera i nie alokuj takiej
ilości rzeczywistych danych tylko po to, by sprawdzić parser rozmiaru.

Zaimplementuj jeden referencyjny backend: libvirt/QEMU/KVM na Linux x86_64.
To przyjęty zakres implementacji, nie rozpoznany model laptopa. Wybór wspieranej
wersji dystrybucji zapisz w ADR wraz z wymaganiami; zgodność potwierdzi później
lokalny `qsx doctor`. Nie wyciągaj takiego wniosku z dystrybucji runnera.

## 2. Architektura, którą da się testować bez hipernadzorcy

Oddziel logikę stanu zadania, manifestów, Git, retencji i planowania od adapterów
wykonujących operacje na dyskach i libvirt. Wspólny model stanu ma być używany
przez CLI i adapter produkcyjny. Nie twórz drugiej, uproszczonej implementacji
wyłącznie na potrzeby testów.

Dostarcz rzeczywisty kod adaptera, jawne błędy i bezpieczne odmowy. `dry-run` nie
może być implementacją wszystkich operacji. Niedokończona ścieżka ma zwracać
jednoznaczny błąd, nigdy fikcyjne powodzenie.

Proponowany stos MVP: Python 3.12, biblioteka standardowa tam, gdzie wystarcza,
wywołania programów z listami argumentów lub oficjalne bindingi libvirt.
Nowe zależności muszą mieć uzasadnienie i lockfile. Nie stosuj `shell=True`
z danymi od użytkownika ani pochodzącymi z repozytorium.

## 3. Macierz dowodów

| Warstwa | Co wykonać teraz | Czego to nie potwierdza |
|---|---|---|
| Unit/property | Schemat TOML, manifesty, nazwy gałęzi, task/run ID, limity, przejścia stanu, błędy i przerwane operacje | Uruchomienia VM |
| Real Git integration | Syntetyczne repo bez commita, czyste, dirty, pliki nowe/usunięte, gałąź, wznowienie, bundle/diff, brak zmiany źródła | Ochrony oryginału przed przejętą VM |
| Real image integration | Tworzenie i kontrola małych, nieaktywnych QCOW2; kopia, checkpoint/restore na danych testowych | Spójności kompletnego, uruchomionego systemu gościa |
| Schema/adapter | Walidacja domen libvirt, struktura wywołań, mapowanie błędów, fake transport | Działania firewalla, KVM ani AppArmor/SELinux |
| Optional TCG | Uruchomienie małego zatwierdzonego gościa bez sieci, test konsoli i start/stop, tylko gdy runner to umożliwia | Akceleracji KVM, wydajności laptopa, izolacji hosta |
| Local acceptance | Później: KVM, realna VM 60 GiB, import do jej filesystemu, odtworzenie, reguły sieci, dysk/RAM, restart hosta | Nie deklarować jako wykonane w chmurze |
| Operator auth | Później: logowanie OpenAI i test dozwolonych połączeń | Nie zastępować mockiem i statusem PASS |

QEMU ma programowy akcelerator TCG [C4]. Może umożliwić ograniczony test startu
bez KVM, jeżeli polityka runnera i dostępne zasoby na to pozwalają. Nie włączaj
TCG jako cichego fallbacku produkcyjnego. Nie obchodź ograniczeń platformy.

`qemu-img` obsługuje operacje na obrazach offline [C5]. Używaj wyłącznie własnych
plików testowych; nigdy nie modyfikuj obrazu używanego przez VM.

`test:///default` w libvirt jest sterownikiem pozorowanym ze stanem per proces
[C6]. Jego wynik jest dowodem testu adaptera, nie uruchomienia VM. Oddzielne
wywołania `virsh` nie tworzą trwałego środowiska testowego tego sterownika.

Samo istnienie `/dev/kvm` i prawo odczytu/zapisu nie dowodzą sprawnego KVM.
Preflight ma odróżniać wykrycie urządzenia od rzeczywistego testu akceleratora.
Brak urządzenia nie zatrzymuje pozostałych klas testów.

## 4. Obowiązkowy zakres chmurowy

Wdroż i wykonaj testy: realny Git, parser/manifesty, bezpieczny import i eksport
na syntetycznych katalogach, realne nieaktywne QCOW2, walidację szablonów i testy
kontraktu adaptera. Brak `qemu-img` jest blokadą tej grupy i problemem środowiska
do poprawienia; nie zmieniaj jej po cichu w opcjonalną.

Testy sieci w chmurze mogą sprawdzać reguły jako dane i decyzje adaptera. Test
izolacji lokalnej wymaga później rzeczywistych połączeń w kontrolowanym labie.
Nie testuj dostępu do obcych systemów ani cudzych zasobów.

Dla T01–T15 z głównej specyfikacji podaj osobno dowody `cloud` i `host`.
Przykład: T07.cloud PASS dla kopii danych dyskowych nie oznacza T07.host PASS
dla odtworzenia całej MathResearch.

Każdy wynik ma: ID, zakres, PASS/FAIL/BLOCKED/NOT_RUN, rodzaj dowodu, polecenie,
kod wyjścia, wersje i ograniczenia. Zapisuj JUnit/JSON plus krótki raport Markdown.
Nie eksportuj sekretów, całego `env`, danych uwierzytelnienia ani poufnych plików.

Proponowane bramki:

- `CLOUD_VALIDATED`: wszystkie obowiązkowe testy chmurowe wykonane i zaliczone;
  adapter produkcyjny zaimplementowany, jawna lista lokalnych testów do wykonania.
- `HOST_VALIDATED`: późniejszy, oddzielny odbiór na konkretnym hoście.

Te nazwy to statusy naszego projektu, nie funkcje wbudowane w Codex.

## 5. Dane i Git w samym zadaniu Cloud

Repozytorium infrastruktury QSX nie wymaga całego badawczego repo FT1536.
Generuj małe fixtures zawierające syntetyczny kod i fikcyjne sekrety do testów
redakcji; nigdy działające tokeny. Nie publikuj faktycznej topologii LAN,
UUID dysków, prywatnych adresów ani konfiguracji laptopa w przykładowych plikach.

Nie pomyl roboczej gałęzi zadania Codex Cloud z funkcją tworzenia gałęzi agenta
wewnątrz przyszłej VM. Implementację QSX zapisuj na roboczej gałęzi udostępnionej
zadaniu, zachowując workflow platformy; nie force-pushuj, nie zmieniaj zdalnych
repozytoriów i nie commituj bezpośrednio do chronionego main.

Baseline i gałąź MathResearch z głównej specyfikacji powstają dopiero w prywatnym
projekcie gościa. Testuj ten mechanizm na niezależnych kopiach syntetycznych repo.

## 6. Środowisko i zależności

Instrukcja ustawienia panelu znajduje się w README. Skrypt `codex/setup.sh`
przygotowuje zależności wyłącznie w runnerze po ustawieniu znacznika kontekstu.
Nie jest instalatorem QSX na laptopie. Zmienna kontekstu i AGENTS.md to instrukcje
oraz zabezpieczenia przed pomyłką, nie granica bezpieczeństwa przeciw agentowi.

Pierwszy bootstrap bez lockfile pobiera narzędzia developerskie i zapisuje
rzeczywiste wersje. To nie jest jeszcze w pełni odtwarzalny build. Następnie
agent ma przygotować i zatwierdzić `requirements-dev.lock` z hashami i kompletem
zależności, sprawdzić czysty setup z tym plikiem oraz zapisać wersje narzędzi
systemowych. Sam raport `pip freeze` nie zapewnia trwałej dostępności artefaktów.
Nie przypinaj wersji przez zgadywanie numerów.

Maintenance sprawdza gotowość i zgodność lockfile; nie uruchamia automatycznie
usług ani nie zmienia zależności. Po zmianie lockfile uruchom kontrolowane setup
ponownie lub zresetuj cache. Nowe zależności podczas zadania wymagają kolejnego
kontrolowanego przygotowania, nie rozszerzenia dostępu do internetu na wszystko.

Nie wymagaj demona libvirt, systemd, bridge/TAP, CAP_NET_ADMIN, nested KVM ani
logowania OpenAI w standardowych testach chmurowych. Brak tych możliwości ma
być rozpoznany, nie obchodzony. Instalator hosta pozostaje oddzielny i nie jest
uruchamiany w tym zadaniu.

## 7. Dostarczenie i odbiór

Oczekiwane są: działający kod QSX, jego testy, konfiguracja przykładowa,
przypięte zależności, raport macierzy testów, `LOCAL_ACCEPTANCE.md` i procedura
bezpiecznego przywracania. Dodaj `make test-cloud` oraz osobny punkt wejścia do
lokalnych testów wymagających jawnej zgody. Żaden cloud target nie może
uruchamiać instalatora hosta.

Pierwsze uruchomienie lokalne: odczytujący preflight → plan zmian → zatwierdzenie
operatora → czysta VM offline z projektem testowym → import/Git/restore/export →
kontrolowana sieć i logowanie → dopiero rzeczywisty projekt.

Brak lokalnego odbioru nie jest obniżeniem wymagań. Nie nazywaj wydania gotowym
do pracy z poufnymi danymi tylko dlatego, że wszystkie unit testy przeszły.

## Źródła techniczne

[C1] OpenAI, Cloud environments: `https://developers.openai.com/codex/cloud/environments`

[C2] OpenAI, Agent internet access: `https://developers.openai.com/codex/cloud/internet-access`

[C3] OpenAI, AGENTS.md: `https://developers.openai.com/codex/guides/agents-md/`

[C4] QEMU, accelerators: `https://www.qemu.org/docs/master/system/introduction.html`

[C5] QEMU, qemu-img: `https://www.qemu.org/docs/master/tools/qemu-img.html`

[C6] libvirt, test driver: `https://libvirt.org/drvtest.html`

Dokumentacja aktualna przy sprawdzaniu 2026-09-05. Strony master opisują także
wersje rozwojowe; implementację dopasuj do faktycznie zainstalowanych wersji.
