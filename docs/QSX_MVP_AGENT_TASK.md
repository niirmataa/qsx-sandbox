# QSX MVP — zadanie dla Codex Cloud

Wersja 1.1-cloud — 5 września 2026 r.  
Status: specyfikacja implementacji, nie istniejący program ani zatwierdzone wdrożenie.

## Obowiązujący tryb wykonania: CLOUD_BUILD

Budujesz QSX w repozytorium infrastruktury, bez dostępu do laptopa właściciela.
Najpierw przeczytaj `AGENTS.md` i `docs/CODEX_CLOUD.md`.
Ten wstęp i dodatek chmurowy zastępują wyłącznie założenia o miejscu wykonania,
wyborze docelowego systemu i bieżącym odbiorze. Wymagania bezpieczeństwa i funkcje
produktu z poniższej specyfikacji nadal obowiązują.

- Katalog główny tego repozytorium jest projektem QSX. Nie twórz drugiego,
  niepotrzebnego repozytorium ani zagnieżdżenia `qsx/qsx`. Standardowy katalog
  źródeł pakietu, np. `src/qsx`, jest dozwolony.
- Nie próbuj czytać lokalnej ścieżki `/media/...` właściciela. W konfiguracji
  przykładowej użyj neutralnej ścieżki; testuj na syntetycznych projektach.
- Rozpoznaj runner chmurowy, ale nie uznawaj jego CPU, RAM, dystrybucji, sieci
  ani dostępnych urządzeń za parametry docelowego laptopa.
- Zaimplementuj prawdziwy adapter libvirt, nie tylko mocki. Brak KVM nie blokuje
  implementacji, testów logiki, integracji Git ani operacji na nieaktywnych QCOW2.
- W tym zadaniu nie wykonuj wdrożenia na rzeczywistym hoście. Przygotuj lokalny
  preflight, plan zmian, kontrolowany instalator i testy odbioru do późniejszego
  wykonania przez operatora. Brak dostępu oznacz przy konkretnym teście.
- Nie dostajesz i nie potrzebujesz teraz kluczy WireGuard/SSH ani tokenów OpenAI
  właściciela. Nie uruchamiaj `codex login` w procesie budowania QSX.
- Celem bieżącego zadania jest działający kod i wynik CLOUD_VALIDATED, o ile
  wszystkie obowiązkowe testy chmurowe przeszły. Nie ogłaszaj HOST_VALIDATED,
  pełnego wdrożenia ani potwierdzonej izolacji bez lokalnego odbioru.

---

Status: specyfikacja do wykonania, nie istniejący program ani raport z testów.  
Bazowe wymagania produktu: wersja 1.0 — 5 września 2026 r.  
Cel pierwszego wdrożenia: jedna VM `MathResearch`, prywatny projekt, gałąź agenta, rygor pracy i odtwarzalne punkty przywracania.

## 1. Polecenie wykonawcze

Zaimplementuj QSX: małe, lokalne narzędzie CLI do obsługi jednego środowiska QEMU/KVM przez libvirt. Nie poprzestawaj na projekcie architektury: dostarcz kod, konfigurację, testy i instrukcję uruchomienia. Rozpocznij od nieinwazyjnego rozpoznania środowiska i implementuj kolejne, testowalne etapy.

Buduj w osobnym katalogu `qsx`, poza źródłowym projektem badawczym. Katalog badawczy jest wejściem tylko do odczytu, a nie miejscem na implementację infrastruktury. Nie zakładaj, że masz już dostęp do docelowego laptopa. Bez KVM lub potrzebnych uprawnień wykonaj prace programistyczne i testy możliwe w bieżącym środowisku, a rzeczywiste uruchomienie oznacz jako BLOCKED albo NOT RUN. Nie przedstawiaj symulacji jako wdrożenia.

Nie wymagaj commita w oryginalnym repo. Nie instaluj Docker Sandboxes, Docker Governance ani zewnętrznego panelu. Nie buduj od razu klastra, wieloagentowości, MCP ani zdalnego pulpitu. Zaprojektuj ich późniejsze dołączenie, ale najpierw zamknij testowalny zakres jednej VM.

Właściciel akceptuje OpenAI jako dostawcę modelu. Nie zastępuj tej decyzji wymaganiem lokalnego modelu. Jednocześnie cały kod wykonywany przez agenta oraz dane otrzymane z sieci pozostają niezaufane.

## 2. Model zaufania i ograniczenia

Zaufana podstawa: dedykowany serwer, jego firmware, kernel, QEMU/KVM, libvirt, chroniona konfiguracja QSX i — docelowo — osobny router. Codzienny laptop jest ograniczonym klientem, nie automatycznie zaufanym administratorem.

Niezaufane: proces agenta, system gościa po rozpoczęciu pracy, pliki projektu, skrypty testowe, zależności, strony internetowe i artefakty zwracane przez VM. Projekt ma ograniczać skutki przejęcia gościa, także gdy atakujący uzyska root wewnątrz VM. Nie obiecuj ochrony przed przejętym administratorem hosta, wszystkimi ucieczkami z QEMU, atakami sprzętowymi ani całkowitej odporności na odmowę usługi. [S4]

Reguły pracy Git i instrukcje agenta są mechanizmem organizacyjnym. Twarde ograniczenia dostępu do hosta, internetu, sekretów administracyjnych i zatwierdzonego oryginału muszą znajdować się poza gościem. Nie nazywaj lokalnego hooka granicą bezpieczeństwa: część hooków można pominąć, a agent kontrolujący kopię może zmienić jej konfigurację. [S3]

W MVP nie ma konta producenta warstwy wirtualizacji, telemetrii QSX, publicznego API zarządzającego ani operacji wymagających chmury do samego startu VM. Pobranie obrazu/pakietów jest osobnym etapem przygotowania. Wywołania modelu OpenAI są świadomie dopuszczonym wyjątkiem od izolacji sieciowej, nie trybem air gap.

## 3. Preflight i ochrona istniejącej maszyny

Zaimplementuj `qsx doctor` jako domyślnie tylko odczytujące sprawdzenie: system, architektura CPU, dostęp do KVM, RAM, wolna przestrzeń i system plików, wersje QEMU/libvirt/Git, dostępne mechanizmy AppArmor/SELinux, aktywne sieci, uprawnienia i istniejące domeny libvirt. Wykrywaj dostępne zasoby; nie wymagaj ponownego podawania parametrów, które narzędzie może odczytać.

Instalacja pakietów i zmiany uprzywilejowane muszą być osobnym krokiem z planem oraz jawnym zatwierdzeniem operatora. Bez kasowania istniejących VM, formatowania dysków, globalnego wyłączania zabezpieczeń, podmieniania sieci hosta, `nft flush ruleset`, szerokiego `chmod 777` ani tworzenia nieograniczonego sudo. Zdalna sesja administracyjna nie może zostać przypadkowo odcięta przez instalator.

Wybierz jeden wspierany system hosta na pierwsze wdrożenie na podstawie preflight. Przypnij i zapisz rzeczywiście przetestowane wersje. Nie implementuj naraz pełnej obsługi wszystkich dystrybucji.

## 4. VM i dysk

Instancja nazywa się `MathResearch`. Ma jeden prywatny dysk QCOW2 o pojemności wirtualnej **60 GiB** na system i projekt. Nie wracaj do podziału woluminów specyficznego dla Docker Sandboxes. Sprawdź również rozmiar partycji i systemu plików wewnątrz gościa: sama zmiana nagłówka obrazu nie kończy powiększania dysku.

Preferencja MVP: samodzielny dysk roboczy utworzony z zatwierdzonego obrazu bazowego, bez długiego łańcucha backing files. Obraz bazowy ma udokumentowane pochodzenie i zweryfikowane sumy/podpisy, o ile producent je udostępnia. Nie zawiera projektu, kluczy ani logowania OpenAI. Nie używaj niezaufanego dysku gościa jako nowej bazy.

Pojemność 60 GiB nie jest limitem całego katalogu QSX. Obrazy bazowe, punkty przywracania, staging i eksport wymagają dodatkowego miejsca. Wprowadź sprawdzanie wolnej przestrzeni, budżet snapshotów i zatrzymanie tworzenia nowych danych przed zapełnieniem hosta. Parametry CPU/RAM ustal z dostępnych zasobów, pozostawiając zapas hostowi; zapisz limity.

QEMU uruchamiaj jako ograniczony użytkownik przez libvirt. Wykorzystaj dostępne ograniczenia systemowe; nie wyłączaj seccomp ani AppArmor/SELinux w celu obejścia błędu konfiguracji. Dokumentuj faktycznie aktywne mechanizmy, nie sam zamiar. [S4, S5]

Nie podłączaj do VM: hostowego projektu przez virtiofs/9p/NFS/SMB, katalogu domowego hosta, fizycznego dysku z projektem, socketów Docker/libvirt, interfejsów QMP hosta, kluczy administracyjnych ani schowka/pulpitu hosta. Nośnik importowy wolno podłączyć tymczasowo tylko do odczytu; odłącz go przed startem agenta. Wszystkie zapisy projektu trafiają na dysk VM.

## 5. Import projektu bez modyfikacji oryginału

Neutralny przykład ścieżki wejścia; rzeczywista ścieżka zostanie ustawiona lokalnie, nie w kodzie:

```text
/srv/qsx-input/PROJECT
```

Docelowy katalog projektu w gościu:

```text
/home/agent/workspace/FT1536_IDEAL_SOURCE_BASELINE_V1
```

Dostarcz trzy obsługiwane przypadki: zwykły katalog bez `.git`, repo zainicjalizowane bez pierwszego commita, repo z historią i dowolnymi niezacommitowanymi zmianami. Obsłuż również czyste repo.

Przy imporcie rozdziel dwa składniki:

1. Opcjonalną historię Git wymaganą do odtworzenia wybranego bazowego commita.
2. Faktyczny stan plików roboczych, obejmujący zmodyfikowane, nowe oraz usunięte pliki.

Sam `git clone` ani bundle nie zastępuje kopii aktualnego drzewa roboczego. Bundle służy do przenoszenia historii, nie stanu indeksu i bieżących plików. [S1, S2]

Wykonuj import przez prywatny staging i kontrolowany transfer do gościa, np. przygotowany nośnik tylko do odczytu albo izolowane narzędzie do obrazu wyłączonej VM. Nie montuj niezaufanego filesystemu gościa bezpośrednio w kernelu hosta. Jeżeli używasz libguestfs, określ format obrazu jawnie, wyłącz zbędną sieć i nie wykonuj programów z niezaufanego gościa w appliance. [S9]

Nie przenoś w ciemno `.git/config`, hooków, credential helperów, konfiguracji filtrów, hostowego `~/.gitconfig` ani `~/.codex`. Odtwórz repo z danych i świeżej konfiguracji. Zakazane są `git worktree` współdzielące oryginalne repo, `clone --shared`, zależności alternates oraz hardlinki do źródła. Lokalny clone może domyślnie używać hardlinków; import ma być niezależny. [S1]

Nie uruchamiaj kodu projektu na hoście podczas importu. Wszystkie operacje Git wykonywane przy granicy hosta mają używać kontrolowanej konfiguracji, bez niejawnego uruchamiania repozytoryjnych hooków, filtrów, aliasów czy poleceń transportowych.

Pliki ignorowane przez Git nie są automatycznie bezużyteczne ani automatycznie bezpieczne. Dodaj jawny profil dołączania/wykluczania oraz manifest. Domyślnie uwzględnij tracked i untracked nieignorowane, a ignorowane zasoby według jawnej listy. Nigdy nie pomijaj po cichu plików śledzonych: wykrycie sekretu w takim pliku albo w importowanej historii ma blokować import do wyjaśnienia, nie udawać zwykłego usunięcia. Domyślnie wyklucz dane logowania, prywatne klucze i lokalne magazyny haseł. Skan sekretów jest heurystyką, nie gwarancją ich braku.

Nie rozwiązuj symlinków prowadzących poza źródło. Obsłuż spacje, Unicode i nietypowe nazwy; odrzuć przejście poza katalog, pliki urządzeń i niebezpieczne dowiązania. Wykryj Git LFS, submodules, partial clones, zewnętrzny gitdir oraz sparse checkout. Jeżeli nie są poprawnie obsługiwane, zakończ z czytelnym błędem; nie uruchamiaj agenta na niepełnej kopii i nie pobieraj danych automatycznie z internetu.

Wykryj zmiany źródła podczas importu. Nie deklaruj atomowego snapshotu katalogu, jeśli jest to tylko zwykłe kopiowanie. Zrób manifest przed/po, wykorzystaj dostępny snapshot filesystemu albo wymagaj przerwania zapisów na czas spójnego importu. Oryginał i jego `.git` pozostają niezmienione; odczyt może zmienić atime, co nie jest naruszeniem treści.

## 6. Baseline i automatyczna gałąź agenta

Każde nowe zadanie ma `task_id`, niezmienny `run_id`, identyfikator VM i unikalną gałąź, np.:

```text
agent/mathresearch/<czas-UTC>-<task-id>
```

Wznowienie zadania wraca na jego dotychczasową gałąź; nie tworzy nowej przy każdym uruchomieniu. Nazwy waliduj jak dane, nie interpoluj do powłoki.

Jeśli źródło ma historię, zachowaj źródłowy commit jako `source_commit`. Na niezależnej kopii w VM nałóż rzeczywisty stan plików wejściowych i utwórz techniczny commit importu, o ile jest potrzebny. Zapisz `baseline_commit` i rozdziel to, co pochodziło od użytkownika, od późniejszych zmian agenta. Bazą gałęzi zadania jest ten baseline.

Jeśli nie ma historii lub pierwszego commita, wykonaj `git init` i commit baseline wyłącznie wewnątrz VM. Dopuszczalny jest pusty baseline dla pustego projektu. Nie zmieniaj globalnego `init.defaultBranch` użytkownika i nie twórz pierwszego commita w oryginale.

Autor technicznego importu ma być odróżnialny od agenta, np. `QSX Import <qsx-import@local.invalid>`. Commity agenta mają osobną tożsamość, np. `QSX Agent MathResearch <qsx-agent@local.invalid>`. Nie podszywaj się pod właściciela. Autor Git jest deklaracją w metadanych, a nie uwierzytelnioną tożsamością.

Zapisz po stronie kontrolera: source/baseline commit, SHA-256 manifestu importu, identyfikator obrazu bazowego, gałąź, run/task ID i wersję reguł. Kopia tych informacji w gościu nie może być jedynym źródłem prawdy. Baseline zachowaj poza zasięgiem zapisu agenta.

Nie konfiguruj poświadczeń push ani aktywnego writable remote do oryginału. Nie twórz gałęzi zadania w oryginalnym repo. Wyniki trafiają do niego dopiero po osobnym przeglądzie właściciela.

## 7. Rygor pracy badawczej i commitowania

Dostarcz wersjonowany szablon reguł agenta oraz mały helper kontrolujący zwykły przebieg pracy. Nie nadpisuj istniejącego `AGENTS.md` źródłowego projektu. Reguły QSX wprowadzaj jawnie po imporcie i odnotuj ich wersję; nie traktuj zmodyfikowanej przez agenta kopii jako autorytatywnej.

Każdy etap pracy ma cel, ograniczony diff, kontrolę wyników oraz mały, logiczny commit. Przed commitem sprawdź gałąź, zakres zmian, przypadkowe sekrety, duże artefakty i wyniki odpowiednich testów. Nie twórz automatycznego `git add -A` co minutę bez przeglądu zawartości.

Format komunikatu może być prosty:

```text
research: sprawdzenie przypadku brzegowego [task-001]

Cel: ...
Sprawdzenie: ...
Wynik: PASS / FAIL / NOT RUN
Ograniczenia: ...
```

Nie ukrywaj błędów przez kasowanie testów ani nie oznaczaj nieuruchomionych testów jako zaliczonych. Przy pracy matematycznej rozdziel dowód, hipotezę i obserwację numeryczną. Zapisuj warunki eksperymentu, wersje narzędzi, parametry i seedy; pojedynczy test numeryczny nie ma być przedstawiany jako dowód ogólnego twierdzenia.

Eksperyment nieudany jest prawidłowym wynikiem badawczym. Wolno go zapisać jako jasno oznaczony commit `experiment:` albo `wip:` z wynikiem FAIL/NOT RUN; nie wolno promować go jako zweryfikowanego rozwiązania. Dla przerwanego zadania zachowaj stan roboczy, zamiast wymuszać fałszywy „zielony” commit.

Bez samowolnego rebase, amend opublikowanych checkpointów, force push, merge do oryginału ani przepisywania baseline. Zwykłe poprawki zapisuj kolejnym commitem lub revertem. Model nie ma prawa zmieniać konfiguracji hosta ani usuwać punktów przywracania.

Helpery i hooki poprawiają dyscyplinę, ale nie powstrzymają przejętego użytkownika VM przed zmianą lokalnej historii. Kontroler weryfikuje eksport względem zachowanego baseline i znanych checkpointów. Nigdy nie akceptuj tylko deklaracji agenta, że testy przeszły. Niezależne odtwarzanie testów wykonuj w odtworzonej VM, nie na hoście. [S3]

## 8. Snapshoty i odzyskiwanie — zakres MVP

Zaimplementuj odtwarzalne checkpointy dyskowe. W MVP wykonuj je **przy wyłączonej VM**, bez zapisu RAM. Akceptowalne są samodzielne kopie QCOW2 po poprawnym zamknięciu albo mechanizm snapshotów libvirt, którego tworzenie i przywracanie zostało sprawdzone na zainstalowanej wersji. Wybierz jeden backend i opisz decyzję; nie buduj równolegle trzech wariantów.

Sekwencja checkpointu: zatrzymaj pracę agenta w kontrolowanym punkcie; zapisz wynik kontroli i Git; poprawnie zamknij system; potwierdź stan SHUTOFF i brak otwartych zapisów; utwórz punkt przywracania; atomowo zapisz manifest; dopiero potem opcjonalnie uruchom ponownie VM. Nie modyfikuj działającego obrazu przez `qemu-img`, `cp` ani `rsync`. QEMU ostrzega przed modyfikowaniem obrazów używanych przez działającą VM. [S6]

Jeśli agent albo gość nie współpracuje, nie deklaruj spójnego checkpointu. Możesz zachować stan jako awaryjny/crash-consistent po jawnym zatwierdzeniu wymuszonego wyłączenia, z wyraźnym oznaczeniem. W MVP nie obiecuj bezprzerwowych live snapshotów. Przyszły wariant live wymaga osobnego projektu spójności filesystemu/aplikacji i testów odtwarzania. [S7]

Automatyczne punkty: po utworzeniu baseline i przed pierwszym uruchomieniem agenta; na granicy zakończenia zadania; przed kontrolowaną aktualizacją środowiska. Ręczny checkpoint jest dostępny w dowolnym bezpiecznym punkcie. Nie twórz pełnego snapshotu po każdym drobnym commicie. W trybie interaktywnym punkt wymagający wyłączenia musi jasno komunikować przerwę; nie wyłączaj bez ostrzeżenia aktywnej sesji użytkownika.

Każdy manifest zawiera: snapshot/run/task ID, VM UUID, gałąź, baseline i checkpoint commit, stan dirty/clean oraz hash manifestu niezacommitowanych plików, status testów, czas UTC, definicję domeny, stan firmware/NVRAM lub informację o jego braku, identyfikatory plików dyskowych oraz rozróżnienie danych obserwowanych przez kontroler i raportowanych przez gościa. ID checkpointu nie jest samą nazwą commita.

Dla niezacommitowanego stanu nie twórz fikcyjnego powiązania „snapshot odpowiada wyłącznie commitowi”. Snapshot obejmuje dysk, a manifest ma jawnie opisywać dodatkowe zmiany. Nie podpisuj ślepo metadanych dostarczonych przez agenta jako wyników niezależnej weryfikacji.

Rollback wymaga zatrzymania VM, potwierdzenia operatora i domyślnie zachowania bieżącego stanu ratunkowego. Brak miejsca ma blokować operację zamiast cichego usunięcia najnowszej pracy. Po przywróceniu najpierw start offline, kontrola projektu, stanu Git, reguł i poświadczeń; wyjście do OpenAI dopiero świadomie ponownie włączone. Nie cofaj hostowego dziennika lifecycle razem z dyskiem VM.

Snapshot nie jest niezależnym backupem. Dostarcz procedurę kopii na drugi nośnik i testu odzyskania. Ustaw konfigurowalną retencję i limit miejsca; domyślnie zachowuj baseline oraz kilka ostatnich punktów, ale nie usuwaj jedynego sprawdzonego punktu bez decyzji właściciela.

Snapshot może zawierać tokeny OpenAI. Przechowuj go jak sekret, poza eksportem wyników i z ograniczonymi prawami. Przywrócenie starego dysku nie cofa wywołań API, kosztów, wysłanych danych ani odwołania/rotacji tokenów. [S8]

## 9. Sieć i Codex

Zaimplementuj dwa jawne profile: `offline` oraz `openai`. Domyślny start nowej VM jest offline. Dla offline preferuj brak karty sieciowej, jeżeli konsola/import nie potrzebuje IP. Nie używaj domyślnej sieci NAT jako substytutu izolacji. Samo libvirt „isolated” może nadal pozwalać na kontakt z hostem i innymi gośćmi. [S10]

`openai` dopuszcza tylko udokumentowany, przetestowany zestaw połączeń niezbędnych do wybranego logowania i pracy Codexa. Nie zgaduj domen ani adresów z pamięci; sprawdź aktualną dokumentację i działanie klienta. Oddziel bootstrap aktualizacji od normalnej pracy. Nie otwieraj całego internetu, gdy brakuje jednej zależności.

Politykę egzekwuj poza VM. Jeżeli używasz lokalnego proxy, ogranicz je do dedykowanego interfejsu, użytkownika i konfiguracji. Agent nie może go ominąć bezpośrednim TCP, DNS, UDP/QUIC ani IPv6. Samo ustawienie zmiennej `HTTPS_PROXY` nie spełnia tego wymagania. Brak proxy lub błędne reguły kończą się odmową, nie automatycznym direct connection. Nie przedstawiaj ograniczenia CONNECT do domeny jako kontroli wszystkich operacji aplikacyjnych tej domeny.

W MVP z jedną VM dopuszczalny jest minimalny zaufany komponent proxy na hoście, z udokumentowanym zwiększeniem powierzchni ataku. Nie udawaj wtedy, że host jest całkowicie nieosiągalny: VM może dojść do dokładnie tego endpointu, a inne usługi mają być zablokowane. Docelowo przewidź przeniesienie tego komponentu na router albo osobne środowisko usługowe.

Nie publikuj portów administracyjnych w LAN/WAN. Unikaj agent forwarding i poświadczeń hosta w gościu. Transport konsoli/shella ma być lokalny albo zainicjowany przez zaufanego kontrolera do VM i ograniczony do konkretnej usługi; nie instaluj w hoście ogólnego endpointu wykonywania poleceń z gościa.

Zainstaluj oficjalny Codex CLI w kontrolowanym etapie przygotowania i zapisz wersję. Logowanie wykonuje właściciel wewnątrz VM, preferencyjnie oficjalnym device-code flow, jeżeli dostępny dla konta (`codex login --device-auth`). Nie obsługuj hasła w skrypcie QSX i nie odczytuj istniejących sekretów z laptopa właściciela. Brak dostępu do konta oznacza test wymagający operatora, nie powód do podszywania się pod niego. [S8]

Tokeny zostają poza repo, Git, logami i eksportem. MVP nie gwarantuje ukrycia tokena przed przejętym użytkownikiem/systemem VM, jeżeli Codex przechowuje go w tym środowisku. To jawne ryzyko, do późniejszego ograniczenia osobnym brokerem poświadczeń. Nie buduj takiego brokera w pierwszym etapie.

Wyłącz wbudowane przeglądanie stron Codexa w konfiguracji sprawdzonej dla zainstalowanej wersji. To uzupełnienie polityki sieci, nie jej zamiennik. Nie wymuszaj modelu z niezweryfikowaną nazwą ani arbitralnego 1M kontekstu: konfiguracja modelu/kontekstu ma być osobna i zgodna z faktycznymi możliwościami konta.

## 10. Eksport i przyjęcie wyników

Eksportuj wyłącznie do nowego katalogu odbiorczego wskazanego przez operatora, poza źródłem. Nie nadpisuj oryginału, nie rób automatycznego merge/cherry-pick i nie pushuj na GitHub ani inne zewnętrzne repo.

Pakiet wynikowy powinien zawierać: historię/zmiany gałęzi zadania względem baseline, manifest plików, wyniki i komendy testów, znane problemy, identyfikatory checkpointów oraz wyraźne oznaczenie dirty/WIP. Gdy nie ma zgodnej historii w oryginale, dostarcz niezależny bundle i diff do przeglądu, zamiast sugerować bezproblemowy merge. Przy historii zawierającej techniczny commit importu oddziel zmiany wejściowe użytkownika od pracy agenta.

Waliduj ścieżki, dowiązania i rozmiary. Nie rozpakowuj nieufnego archiwum jako root i nie odtwarzaj automatycznie `.git/hooks`, konfiguracji credential helperów, uprawnień SUID, urządzeń ani usług systemowych. Nie uruchamiaj wyeksportowanych skryptów na hoście. Zmienione workflow, hooki, zależności i ustawienia IDE wyróżniaj w raporcie przeglądu.

Kontroler ma sprawdzać rzeczywisty eksport, nie tylko raport agenta. Kod i dokumentacja z VM pozostają nieufne aż do przeglądu właściciela. Przyjęcie zmian do oryginału jest osobnym etapem poza MVP.

## 11. Docelowy interfejs CLI do zaimplementowania

Poniższe polecenia są kontraktem projektowanego QSX, nie komendami istniejącego produktu:

```bash
qsx doctor
qsx plan --config ./qsx.toml
qsx create MathResearch --config ./qsx.toml
qsx auth MathResearch
qsx task start MathResearch --id task-001 --description "Opis zadania"
qsx run MathResearch codex
qsx status MathResearch
qsx shell MathResearch
qsx checkpoint create MathResearch --label after-task-001
qsx checkpoint list MathResearch
qsx checkpoint restore MathResearch --id CHECKPOINT_ID
qsx export MathResearch --output ./exports/task-001
qsx stop MathResearch
```

`task start` przygotowuje gałąź przed agentem. `run` wymaga aktywnego zadania albo tworzy je według jawnej konfiguracji. `auth` nie otwiera sieci bez właściwego profilu i nie przejmuje roli właściciela konta. `checkpoint restore` ma potwierdzenie. Destrukcyjne usuwanie całej VM, jeśli dodasz je do MVP, ma osobne potwierdzenie oraz kontrolę istniejących wyników.

Domyślny plan musi pokazać pliki, woluminy, reguły i uprawnienia, które zostaną zmienione. Używaj strukturalnych wywołań API i list argumentów, nie `shell=True` z danymi projektu. Dodaj blokady współbieżnych operacji, atomowe zapisy manifestów, jednoznaczne kody wyjścia i bezpieczne ponawianie przerwanego `create`. Istniejąca domena o tej samej nazwie nie może być nadpisana.

Przykładowa konfiguracja projektowa — schemat należy zdefiniować i walidować:

```toml
schema_version = 1
name = "MathResearch"
source = "/srv/qsx-input/PROJECT"
workspace = "/home/agent/workspace/FT1536_IDEAL_SOURCE_BASELINE_V1"
disk_gib = 60
network_profile = "offline"
agent = "codex"

[git]
branch_prefix = "agent/mathresearch"
source_read_only = true
allow_push = false

[checkpoints]
mode = "cold"
retain_recent = 5
protect_baseline = true
```

Sekrety nie należą do tego pliku. Wartości CPU/RAM, storage i dopuszczone endpointy mają znaleźć się w konfiguracji po preflight, bez arbitralnego zakładania parametrów starego laptopa.

## 12. Plan rozwoju — udokumentuj, nie wdrażaj teraz

Etap 1: zakres tego dokumentu, jedna VM i lokalny CLI.

Etap 2: jedno wejście VPN na osobnym routerze oraz wymienna VM `WorkDesk` jako zdalny pulpit roboczy. Daje dostęp do sesji/projektów, ale nie przechowuje kluczy administratora hosta. W przyszłości osobny `AdminDesk` albo osobna tożsamość i kanał administracyjny. Wspólny punkt sieciowego wejścia nie oznacza wspólnych uprawnień. Przejęcie brudnego pulpitu nie może oznaczać prawa do usuwania VM, snapshotów lub zmieniania firewalla.

Etap 3: lokalne MCP, wiadomości, zadania i kontroler tur. Autoryzacja operacji po stronie usług; agent nie jest administratorem orkiestracji. Tokeny i tożsamości oddzielne dla agentów. Nie wystawiaj całego API libvirt przez MCP.

Etap 4: osobna VM `BrowserTest` z kontrolowanym internetem, bez bezpośredniego dostępu do badawczego repo. Pobrane treści wracają jako niezaufane dane, a nie instrukcje administracyjne. Ruch przez usługę przeglądarki jest również kanałem wyjścia i podlega regułom; nie nazywaj takiej konfiguracji absolutnym odcięciem od internetu.

Etap 5: kolejni agenci, niezależna weryfikacja wyników, zdalny odbiór artefaktów, backup poza serwerem. Nie zakładaj, że stary laptop uruchomi wszystkie role równolegle: na początku dopuszczaj działanie sekwencyjne.

## 13. Testy odbiorcze

Każdy test ma wynik PASS, FAIL, BLOCKED albo NOT RUN i zapis warunków. Testuj wyłącznie własne środowisko. Brak dostępu do infrastruktury nie upoważnia do deklaracji zaliczenia.

| ID | Wymagany dowód |
|---|---|
| T01 | Czysty katalog bez `.git` jest importowany; baseline i gałąź powstają w VM, nie w źródle. |
| T02 | Repo bez commita działa bez zmian w źródłowym `.git`. |
| T03 | Repo z historią i zmianami staged/unstaged, untracked oraz usunięciami zachowuje ustalony stan wejściowy; źródłowy HEAD pozostaje ten sam. |
| T04 | Manifest treści źródła i kontrola `.git` przed/po są zgodne; nowe commity istnieją tylko w VM. |
| T05 | Próby dostępu do katalogu źródłowego, socketów libvirt/Docker i administracji hosta z gościa nie dają tego dostępu. Sprawdź także definicję domeny i brak współdzielonych mountów. |
| T06 | Start zadania tworzy unikalną gałąź; wznowienie jej nie duplikuje. WIP/FAIL nie otrzymuje statusu zweryfikowanego wyniku. |
| T07 | Checkpoint i rollback odtwarzają pliki, Git i stan środowiska; hostowy dziennik zachowuje fakt przywrócenia. Sprawdź również przypadek dirty workspace. |
| T08 | Operacja snapshotu na aktywnym obrazie jest odrzucana lub VM zostaje kontrolowanie wyłączona; brak cichego `qemu-img` na działającym dysku. |
| T09 | Offline nie ma wyjścia sieciowego. Profil OpenAI działa dla autoryzowanego testu i blokuje zwykły internet, host-admin, LAN, bezpośredni DNS i alternatywne drogi IPv6/UDP. Awaria proxy oznacza blokadę. |
| T10 | Eksport trafia do świeżego katalogu, jest sprawdzany względem baseline i nie uruchamia niczego ani nie modyfikuje źródła. |
| T11 | Odrzucane są niebezpieczne ścieżki, symlinki poza katalog, nazwy przypominające argumenty, nietypowe obiekty plikowe i kolizja nazwy VM. |
| T12 | Brak KVM, uprawnień, miejsca, niezgodna wersja lub nieobsługiwany submodule/LFS daje czytelny błąd zamiast niepełnego sukcesu. |
| T13 | Przerwane create/checkpoint można bezpiecznie wznowić lub posprzątać; nie kasuje istniejących zasobów użytkownika. |
| T14 | Sekrety nie występują w Git, konfiguracji eksportowej ani logach; snapshoty mają ograniczony dostęp i są oznaczone jako potencjalnie zawierające tokeny. |
| T15 | Kopia punktu przywracania na osobny nośnik ma udokumentowany, rzeczywiście wykonany test odtworzenia albo jawne NOT RUN. |

Nie interpretuj tych testów jako dowodu braku podatności hipernadzorcy. Pokazują poprawność sprawdzonych granic i zachowań w określonej konfiguracji.

## 14. Oczekiwane pliki i raport końcowy

Dostarcz kod CLI, schemat i przykład konfiguracji, testy, szablon zasad agenta, skrypty przygotowania gościa, README oraz dokumenty `THREAT_MODEL.md`, `ARCHITECTURE.md`, `RECOVERY.md` i raport wykonanych testów. W dokumentach odróżnij funkcje gotowe, ograniczenia i plan kolejnych etapów.

Raport końcowy ma podać: rzeczywiste wersje narzędzi, zmodyfikowane pliki/systemowe zasoby, komendy uruchomienia, miejsce danych VM, działający przebieg import → gałąź → commit → checkpoint → restore → export, oraz wszystkie testy FAIL/BLOCKED/NOT RUN. Nie wpisuj do raportu tokenów ani pełnych poufnych promptów.

Gdy dostępne są uprawnienia i operator zatwierdził zmiany, uruchom instancję testową, wykonaj testy, a następnie przygotuj `MathResearch`. Logowanie do OpenAI pozostaw właścicielowi. Gdy uprawnień brakuje, dostarcz gotowe pliki oraz dokładnie wskazany krok wdrożeniowy; nie zastępuj całej implementacji ogólną poradą.

## 15. Dokumentacja źródłowa do sprawdzenia podczas implementacji

To punkty odniesienia, nie zastępstwo kontroli wersji zainstalowanej na docelowym hoście. Powyższe wartości i interfejs QSX są wymaganiami projektu; nie są cytatami z istniejącego produktu.

[S1] Git clone — niezależność obiektów, hardlinki, alternates, współbieżna modyfikacja źródła: `https://git-scm.com/docs/git-clone`

[S2] Git bundle — przenoszenie historii i brak kopii bieżącego working tree: `https://git-scm.com/docs/git-bundle`

[S3] Git hooks — sposób działania i możliwość pominięcia części hooków: `https://git-scm.com/docs/githooks`

[S4] QEMU security — nieuprzywilejowany proces i warstwy ochrony: `https://www.qemu.org/docs/master/system/security.html`

[S5] libvirt, ograniczenia QEMU i ryzyka passthrough: `https://libvirt.org/kbase/qemu-passthrough-security.html`

[S6] QEMU image utility — operacje offline i ostrzeżenie dotyczące działającej VM: `https://www.qemu.org/docs/master/tools/qemu-img.html`

[S7] libvirt snapshots: `https://libvirt.org/formatsnapshot.html` oraz backup/live consistency: `https://libvirt.org/kbase/live_full_disk_backup.html`

[S8] Oficjalne uwierzytelnianie Codex, headless i ochrona auth cache: `https://developers.openai.com/codex/auth/`

[S9] libguestfs — dostęp do dysków i bezpieczeństwo: `https://libguestfs.org/guestfs.3.html` oraz `https://libguestfs.org/guestfs-security.1.html`

[S10] libvirt network — isolated nie oznacza automatycznie braku kontaktu z hostem: `https://libvirt.org/formatnetwork.html`
