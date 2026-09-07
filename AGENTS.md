# QSX Alpine Sandbox V1 — instrukcja lokalnej kontynuacji

Operator uruchamia GPT-6 Astra ULTRA w Codex CLI w katalogu
`QSX-ALPINE-SANDBOX-V1`. Ten katalog zawiera przekazaną paczkę pracy;
nie zakładaj, że sam jest repozytorium Git. Host to Ubuntu 24.04 LTS lub
Debian 13, x86_64 z QEMU/KVM i libvirt. Bieżąca budowa ma zostać wykonana
na dostępnym Ubuntu; rozpoznaj faktyczny system przed zmianami.
System gościa ma pozostać Alpine Linux.

## Cel i obowiązujący zakres

Dokończ samodzielne narzędzie `qsx-alpine`: receptura zadania + profil agenta
→ zamrożone wejścia → zbudowany obraz Alpine → prywatna instancja → konsola
→ działające narzędzia i zadanie w gościu. Doprowadź istniejącą implementację
do rzeczywistego odbioru lokalnego. Naprawiaj wykryte błędy w ramach zadania.
Nie kończ na planie, dokumentacji, mockach ani samym `--version`.

- Pracuj na `feat/alpine-task-images`, zachowując istniejący kod i historię.
- `archive/qsx-core-pr1-3eb4908` jest materiałem odniesienia, nie zależnością
  wykonawczą buildera. Nie przepisuj projektu i nie przywracaj zależności od
  starego kontrolera, aby skorzystać z pojedynczej funkcji.
- Format TOML, istniejące CLI i układ pakietu są punktem kontynuacji.
  Nowy szablon z innymi zależnościami nie może wymagać zmiany kontrolera.
- Receptura zadania, profil agenta i polityka operatora mają osobne role.
  Zasoby i wybór programu/modelu dopasowuje się do zadania; nie twórz
  zamkniętego katalogu czterech ról ani obowiązkowego jednego profilu.
- Priorytetem jest użyteczny, odtwarzalny warsztat. Mierz i poprawiaj czas
  budowy/startu oraz zużycie zasobów, zachowując potrzebne narzędzia.
- Dostarcz osobny działający wariant SageMath + Lean 4. Nie instaluj tych
  narzędzi we wszystkich obrazach. Dalsze zestawy narzędzi i właściwe badania
  kryptograficzne powstaną później.

Aktualne polecenia operatora mają pierwszeństwo przed tym dokumentem i starszymi
specyfikacjami. Historyczne dokumenty nie rozszerzają bieżącego zadania.
Nie zmieniaj instrukcji ani polityk w celu ukrycia błędu lub obejścia uprawnień.

**Poza zakresem:** rozwój Research Memory, integracja starego CLI, aktualizacja
PR #1, merge, panel, klaster, MCP, privAI oraz projekty Main API/iOS.
Pierwotny szeroki scenariusz obejmował checkpoint/rollback i odtwarzanie kontekstu;
obecna samodzielna iteracja obrazów ich nie realizuje. Nie dopisuj ich tutaj
z historycznego kontrolera i nie raportuj pełnej pierwotnej demonstracji jako
zaliczonej. Zwykła notatka przekazania pracy nie jest implementacją Research Memory.

## Najpierw ustal faktyczny stan

1. Znajdź pliki dostawy w tym katalogu. Przeczytaj `START_HERE_PL.md`,
   `HANDOFF_ASTRA_ULTRA_PL.md`, `DELIVERY_MANIFEST.json` i listy sum.
   Sprawdź istniejące rozpakowane katalogi przed tworzeniem kolejnych.
2. Jeśli trzeba odtworzyć repo, wykonaj procedurę z `START_HERE_PL.md`:
   zweryfikuj archiwa, rozpakuj do wolnego miejsca, zweryfikuj zawartość,
   odtwórz mirror z `qsx-alpine-full-history.bundle`, sprawdź `git fsck`
   i sklonuj gałąź roboczą. Mirror zachowuje wszystkie dostarczone refy.
   `qsx-delivery/source/` jest kopią do przeglądu; pracę kontynuuj w repo z historią.
3. Jeśli repo już istnieje, sprawdź `git status`, gałąź, HEAD, tree i lokalne
   commity. Nie resetuj nowszego stanu do wersji dostawy. Nie nadpisuj zmian
   operatora, nie używaj `git reset --hard`, `git clean -fd` ani force-push.
4. W repo przeczytaj: `ACTIVE_SCOPE_PL.md`, `templates/README.md`,
   `docs/WORK_VALIDATION_PL.md`, `docs/HANDOFF_ASTRA_ULTRA_PL.md`,
   `docs/DEBIAN_RUNBOOK_PL.md` i `docs/SAGE_LEAN_TEMPLATE_PL.md`.
   Sprawdź także aktualne CLI, `Makefile` i dokumenty dotyczące zmienianego kodu.
   Runbook Debiana jest punktem wyjścia: dostosuj wymagające tego kroki do
   Ubuntu 24.04 LTS i zapisz sprawdzoną procedurę dla rzeczywistego hosta.
   Nie wymagaj zmiany systemu na Debian tylko z powodu nazwy dokumentu.
5. Dostarczony rootowy `AGENTS.md` opisuje starszą pracę **Codex Cloud**.
   W ramach aktualizacji instrukcji dla lokalnego zadania zachowaj jego dokładną
   kopię jako `docs/history/AGENTS_CODEX_CLOUD.md`, oznacz jej historyczną rolę
   w dokumentacji, a aktywny `AGENTS.md` w repo uaktualnij do tej instrukcji.
   Nie nadpisuj istniejącej kopii historycznej. Jeśli aktywny plik zawiera nowsze
   instrukcje operatora, zachowaj je i uzgodnij rzeczywiste konflikty.

Punkt odniesienia przekazanej paczki, nie nakaz cofania repo:

| Pole | Wartość |
|---|---|
| HEAD dostawy | `fbaeca1686e4829bf68f68e9d2a4862bce7e6576` |
| Tree dostawy | `e58299de539c8057046eb2d062fa7fb5528b75cb` |
| Commit sprawdzonej implementacji | `d477bd5c8b9cea61a903ccd4a6818858185c7d2d` |
| Dostępna historia w bundle | 17 commitów; zachowaj wszystkie dostarczone refy |

W Work raportowano 115 testów PASS, Ruff, mypy i instalację wheela poza źródłami
na Pythonie 3.12. To wyniki historyczne. Nie wykonano budowy/bootu VM, odbioru
KVM, działania sandboxu Codexa w gościu ani zalogowanej pracy modelu.
`qsx-alpine-input-cache.tar.gz` zawiera wejścia do budowy, nie gotowy obraz QSX.
Dokładne bieżące locki dostawy wskazuje `evidence/INPUT_FREEZE.json`.

## Sposób pracy i granice operatora

- Samodzielnie wykonuj autoryzowane, odwracalne prace: odczyty, diagnostykę,
  poprawki repo, testy, budowanie i uruchamianie dedykowanych instancji zadania
  w dostępnych uprawnieniach. Nie pytaj ponownie o zgodę na ustalony zakres.
- Przed uprzywilejowaną zmianą pakietów hosta, usług, grup, ACL lub sieci
  przygotuj konkretny zakres i sprawdź wcześniej udzieloną zgodę operatora.
  Jeśli jej brakuje, przedstaw gotowe polecenia i wyjaśnij potrzebny wpływ.
  Zwykły błąd integracji nie jest powodem do przerzucania naprawy na operatora.
- Polityka bezpieczeństwa i limity pozostają pod kontrolą operatora.
  Nie zmieniaj ich po cichu, nie wyłączaj AppArmor/seccomp i nie stosuj
  `chmod 777`, uprzywilejowanego uruchomienia całego buildera ani automatycznego
  obejścia sandboxu klienta jako naprawy odmowy dostępu.
- Kod `prepare`, dodatkowe archiwa i zadania importowanych projektów wykonuj
  w gościu. Nie uruchamiaj kodu badawczego na hoście kontrolera.
  Odbiór wykonuj na syntetycznym projekcie, bez wymagania prywatnego repo badań.
- Nie dodawaj hostowych mountów projektu ani dostępu do socketa Docker hosta.
  Używaj przewidzianego importu do prywatnej instancji.
- Nie kopiuj tokenów, sesji logowania ani kluczy hosta do wzorców, cache,
  commitów i raportów. Nie odczytuj całego `env`. Wzorzec musi pozostawać wolny
  od danych projektu, poświadczeń i tożsamości konkretnej instancji.
- Zachowuj krótkie aktualizacje postępu i logiczne commity na gałęzi roboczej.
  Nie wykonuj push, zmian PR ani merge bez odrębnego polecenia operatora.

## Kolejność wykonania

### 1. Host i narzędzie

Sprawdź `/etc/os-release`, architekturę, CPU, RAM, wolne miejsce, `/dev/kvm`, QEMU,
systemowy libvirt, konto usługi QEMU oraz dostęp do wybranych ścieżek.
Uruchom `qsx-alpine doctor`. Nie przenoś ograniczeń dawnego runnera na ten host.
TCG może być jawnym testem pomocniczym; nie zalicza odbioru KVM.

Ubuntu 24.04 LTS i Debian 13 są dopuszczonymi hostami. Sprawdź rzeczywiste
pakiety, wersje usług i politykę AppArmor; nie zakładaj identycznej konfiguracji.
Jeśli kod niesłusznie odrzuca Ubuntu wyłącznie po nazwie dystrybucji, popraw
wykrywanie możliwości i potrzebną integrację, zachowując kontrole bezpieczeństwa.

Przygotuj venv i zależności według runbooka. Dobierz lock developerski do
rzeczywistego Pythona: `requirements-dev.lock` dla 3.12 (standardowo Ubuntu 24.04),
`requirements-dev-py313.lock` dla 3.13 (standardowo Debian 13).
Nie zmieniaj systemowego Pythona. Wykorzystaj dostarczony wheelhouse.
Uruchom `make check`; obejmuje testy, Ruff, mypy, wheel i instalację poza źródłami.
Po zmianie kodu odśwież używany executable/wheel, aby demo nie uruchamiało starej
wersji. Błędy naprawiaj; nie wycinaj testów ani nie osłabiaj asercji dla zielonego
wyniku. Dodaj sensowną regresję tam, gdzie wykryto błąd działania.

### 2. Wejścia, build i cache

Używaj istniejących receptur oraz ogólnych mechanizmów `template-new`,
`template-check`, `template-lock`, `image-build` i `image-verify`.
Dokładne argumenty sprawdź przez `--help` i w runbooku; nie wymyślaj komend.

Pozyskanie wejść i weryfikacja pochodzenia odbywają się jawnie podczas freeze.
Budowa ma korzystać z kompletnego cache bez sieci gościa. Brak wejścia lub zły
hash jest błędem. Nie naprawiaj go przez `latest`, pominięcie podpisów/TLS ani
doraźne pobieranie w fazie offline. Zmiana receptury, profilu lub kodu objętego
lockiem wymaga nowego locka. Zachowaj poprzednie wejścia i dowody.

Cache, obrazy i wyniki umieść poza repo, w osobnych katalogach. Używaj nowych
nazw wyjściowych. Zasoby receptury są żądaniem sprawdzanym przez osobną politykę;
nie zwiększaj limitów i nie obniżaj żądanych zasobów bez jawnej konfiguracji.

### 3. Rzeczywista demonstracja na Ubuntu 24.04 LTS lub Debianie 13

Wykorzystaj `scripts/demo_debian.py`: najpierw `--help` i `--plan`, następnie
`--run --prepare-only`, przegląd potrzebnego dostępu storage i `--continue`
zgodnie z runbookiem. Nie zakładaj, że skrypt już pokrywa wszystkie kryteria.
Nazwa `demo_debian.py` nie ogranicza docelowego hosta do Debiana; sprawdź
wykonanie na Ubuntu i napraw rzeczywiste założenia zależne od dystrybucji.

Wykonaj i zapisz dowody dla następujących sprawdzeń:

1. Pierwszy rzeczywisty build Alpine, boot przez KVM i gotowość gościa.
2. Ponowna publikacja do nowej ścieżki z tego samego locka/cache:
   `cache_hit=true`, weryfikacja obrazu i zgodność hashy. Użycie zachowanego
   obrazu nie jest dwiema niezależnymi kompilacjami ani dowodem powtarzalności bitowej.
3. Nowy szablon z inną zależnością i działającym zadaniem, bez zmiany kontrolera.
4. Dwie działające instancje z osobnymi dyskami. Zapis w jednej nie pojawia się
   w drugiej ani w źródle; hash obrazu wzorcowego pozostaje niezmieniony.
5. Import, `finalize-import`, odłączenie nośnika i lokalna konsola szeregowa.
   Program agenta i zadanie działają jako nie-root `agent` we właściwym workspace.
6. Faktyczne rozmiary partycji i filesystemu, użytkownik konsoli, kontrolowany
   shutdown ACPI i brak niezamierzonych hostowych mountów.
7. Egzekwowanie wybranej polityki sieci poza gościem oraz działanie konsoli po
   `network-cut`. Test VM od początku offline nie dowodzi przerwania aktywnego
   połączenia internetowego; takie twierdzenie wymaga osobnego wykonania.

Po błędzie zachowaj artefakty i logi. Sprawdź rzeczywisty stan instancji przed
wznowieniem. Timeout zadania o skutkach ubocznych nie uprawnia do ślepego ponowienia.

### 4. SageMath + Lean 4

`templates/sage-lean4.toml` ma w dostawie status `EXPERIMENTAL_NOT_BUILT`:
SageMath 10.8, Lean 4.33.1 oraz pomocnicze narzędzia. Mathlib nie jest dołączony.
Pełne archiwum Sage nie znajduje się w dostarczonym cache; pozyskaj je w fazie
freeze i sprawdź przypięte wejścia. Wersje są punktem startowym, nie poleceniem
aktualizacji do najnowszych wydań.

Doprowadź recepturę do działającego warsztatu, dobierając jawnie RAM, CPU,
dysk i timeout do hosta. Przykładowe limity polityki nie są pomiarem wymagań.
Naprawiaj konkretne błędy kompilacji i integracji. Sprawdź w uruchomionym gościu
przynajmniej dokładne obliczenie Sage, kompilację prostego dowodu Lean i działanie
Lake jako nie-root; samo wypisanie wersji nie wystarcza.

Jeśli natywna kompilacja jest przeszkodą, operator dopuścił osobną recepturę
z Dockerem **wewnątrz VM Alpine**. Ten wariant nie jest jeszcze zaimplementowany.
Możesz go wykonać w ramach tego zadania: przypnij obrazy digestami, zachowaj
archiwa OCI/Docker jako wejścia, ładuj je offline i sprawdź dostęp narzędzi dla
agenta. Nie podłączaj socketa Docker hosta. Opisz faktyczny runtime narzędzi;
nie przedstawiaj tego wariantu jako natywnej kompilacji na musl.

Nie wymagaj dwóch pełnych implementacji, jeśli jedna spełni cel. Zachowaj
wyniki prób; warianty porównuj na tych samych zadaniach i rzeczywistych pomiarach.

### 5. Codex w gościu i logowanie

W dostawie runner egzekwuje wyłącznie offline; transport wyjścia do OpenAI
nie jest zaimplementowany. Dokończenie zalogowanego zadania wymaga wdrożenia
i sprawdzenia transportu zgodnego z jawnie wybraną polityką operatora.
Nie traktuj braku tego kodu jako braku hasła i nie nazywaj zwykłego NAT-u
lub samego proxy egzekwowaną allowlistą. Nie zmieniaj sieci wszystkich receptur.

Najpierw sprawdź klienta i jego lokalny sandbox w gościu. Ustal, czy operator
już wybrał i zatwierdził politykę wyjścia; zrealizuj ją w udzielonym zakresie.
Jeśli brakuje decyzji, przygotuj konkretną konfigurację i przedstaw operatorowi
jej wpływ przed wdrożeniem. Przetestuj zarówno dozwolone, jak i niedozwolone
połączenia zgodnie z wybraną polityką.
Sprawdź wspierany sposób uwierzytelnienia dla używanej wersji klienta.
Osobiste logowanie operator wykonuje w prywatnej instancji. Nie kopiuj jego
istniejących poświadczeń i nie zapisuj zalogowanej instancji jako wzorca dostawy.

Po logowaniu wykonaj rzeczywiste małe zadanie modelu w gościu, sprawdź wynik
i zapisz dowód bez sekretów. Test lokalnego sandboxu, powłoka Python i
`codex --version` nie zastępują tej sesji. Gdy potrzebny jest operator,
dokończ równolegle wszystkie niezależne prace zamiast kończyć całe zadanie.

## Raportowanie i przekazanie

Każde sprawdzenie ma status, polecenie, aktualny commit/stan drzewa, środowisko,
kod wyjścia, log i krótki opis tego, co rzeczywiście potwierdza:

| Status | Znaczenie |
|---|---|
| `PASS` | Wykonane sprawdzenie zakończyło się powodzeniem w podanym zakresie. |
| `FAIL` | Błąd kodu lub integracji; napraw go i wykonaj właściwy test ponownie. |
| `BLOCKED` | Konkretna brakująca możliwość lub uprawnienie dla tego sprawdzenia. |
| `NOT_RUN` | Sprawdzenie nie zostało wykonane. |
| `AWAITING_OPERATOR_LOGIN` | Przygotowany krok czeka na osobiste logowanie operatora. |
| `NOT_IMPLEMENTED` | Wymaganej funkcji jeszcze nie ma; nie jest to test zaliczony. |

Oddziel testy jednostkowe/kontraktowe, operacje na nieaktywnych dyskach,
rzeczywisty build i boot, odbiór KVM/izolacji, narzędzia badawcze oraz sesję modelu.
W raporcie podaj dystrybucję, jej wersję i kernel. Odbiór na Ubuntu 24.04 LTS
nie zalicza odbioru Debiana 13 i odwrotnie. Drugi host oznacz `NOT_RUN`, jeśli
nie był dostępny; nie blokuje to zakończenia odbioru na wybranym hoście.
Raport `PASS_LOCAL_DEMO_WITH_EXPLICIT_EXCLUSIONS` zachowuje swoje wyłączenia.
Nie ogłaszaj całego zadania ukończonym, gdy wymagany element pozostaje niewykonany.

Dostarcz operatorowi:

- logiczne commity, pełną dostępną historię/refy i aktualny Git bundle;
- działające receptury, profile, polityki, locki, komplet potrzebnych wejść
  lub jasno opisany zakres osobnego cache oraz sumy kontrolne;
- rzeczywiście zbudowane QCOW2 wraz z `.manifest.json` i `.boot.log`, bez sekretów;
- raporty demonstracji i testów, sprawdzone komendy odtworzenia oraz pomiary
  czasu freeze/build/start/cache, rozmiaru wirtualnego/pliku/zajętych bloków
  i skonfigurowanych zasobów; zużycie rzeczywiste oznaczaj osobno;
- krótką aktualną notatkę: co działa, co poprawiono, co pozostało, dokładna
  przyczyna blokady i następna konkretna komenda.

Przekazanie ma pozwalać operatorowi otworzyć pliki i odtworzyć wynik. Na jego
hoście wskaż trwały katalog i sumy; w środowisku zdalnym użyj dostępnego mechanizmu
pobrania. Sama niedostępna ścieżka w runnerze nie jest dostarczeniem artefaktu.
Nie usuwaj dokumentów, historii, wcześniejszych wyników ani dowodów błędów.
