# QSX — Research Memory v1
## Zadanie wdrożeniowe: CHANGELOG / MEMORY / LIFE / CONTEXT

**Status dokumentu:** specyfikacja do implementacji, nie działający moduł.
**Repozytorium:** `niirmataa/qsx-sandbox`.
**Punkt odniesienia:** PR #1, HEAD `a2954f6e9560dbd4afc6e1eb0fe818ad4c782853`, odczytany 2026-09-05.
**Odbiorca:** agent implementujący QSX w Codex Cloud bez dostępu do serwera operatora.

## 1. Cel i zakres

Dodaj niewielką, lokalną warstwę pamięci badawczej, która uzupełnia Git i checkpointy. Kolejny badacz lub agent ma ustalić: co badano, dlaczego wybrano daną metodę, co wykonano, jakie istnieją dowody, czego nie sprawdzono oraz od jakiego rzeczywistego stanu należy kontynuować.

Nie wystarczy utworzyć czterech pustych plików Markdown ani dopisać agentowi ogólnej instrukcji „aktualizuj pamięć”. Dostarcz model danych, trwały zapis, generowanie widoków, integrację z lifecycle i testy. Nie buduj RAG, bazy wektorowej, panelu, MCP, sieci ani uniwersalnej platformy obserwowalności.

Kontynuuj istniejący kod. Najpierw odczytaj bieżący HEAD i poprzednie uwagi PR; nowszych zmian nie cofaj do wskazanej wyżej rewizji. Nie scalaj PR ani nie wykonuj force-push. Dziel zmiany na małe commity. Nie oznaczaj dotychczasowych blockerów jako rozwiązanych samym dodaniem dokumentacji.

Rozdziel dwie rzeczy:
- pamięć pracy nad oprogramowaniem QSX w tym repozytorium;
- funkcję produktu, która utworzy oddzielną historię dla każdego projektu badawczego uruchamianego w VM.

Nie włączaj danych FT1536 do testów ani nie wymagaj prywatnych danych badawczych. Używaj syntetycznych projektów i sztucznych wyników.

## 2. Ustalenia ze statycznego przeglądu punktu odniesienia

Te obserwacje dotyczą wskazanego SHA, nie są wynikami uruchomienia testów:

1. `workspace.py` tworzy `task.json` z identyfikatorem zadania, `run_id`, gałęzią i baseline. Nie zawiera trwałego dziennika decyzji i eksperymentów.
2. `cli.py` ma już lifecycle VM. `resume` wywołuje jednak `import_project()`, a istniejące zadanie przełącza gałąź w hostowym katalogu importowym. To nie jest wiarygodna obserwacja aktualnego workspace gościa.
3. `disk.py` pobiera metadane przez `WorkspaceInspector`. Rodzicem checkpointu zostaje ostatni chronologicznie znaleziony manifest, a nie jawny aktywny punkt po ewentualnym rollbacku. Rollback nie dopisuje trwałej historii ani nie aktualizuje takiego punktu odniesienia.
4. `inspect_offline_workspace()` w `provision.py` hashuje tekst `git status --porcelain=v1`, a nie zmodyfikowane dane. Tego hasha nie używaj jako odcisku zawartości workspace.
5. `AGENTS.md` zawiera zasady implementacji i raportowania, ale nie ma procedury tworzenia i wczytywania pamięci badawczej.
6. W odczytanym drzewie brak modułu dziennika badawczego i czterech opisanych poniżej widoków.

Najpierw sprawdź aktualność tych obserwacji. Napraw odpowiednie miejsca integracji zamiast tworzyć równoległe, sprzeczne źródła stanu.

## 3. Cztery widoki jednego zbioru danych

W eksporcie i zatwierdzonym pakiecie kontekstu twórz katalog `research/`:

| Plik | Znaczenie |
|---|---|
| `CHANGELOG.md` | Istotne zmiany kodu, środowiska i metody badawczej, ich powody oraz wpływ na wyniki. Odsyła do kroków i commitów, nie kopiuje całego `git log`. |
| `MEMORY.md` | Uporządkowane definicje, założenia, hipotezy, obserwacje, decyzje i wyniki negatywne z dowodami, statusem i zakresem stosowalności. |
| `LIFE.md` | Chronologia życia zadania: sesje, commity zaobserwowane przez kontroler, eksperymenty, awarie, checkpointy, rollbacki, przekazania i eksporty. Zachowuje także porzucone ścieżki. |
| `CONTEXT.md` | Zwięzły pakiet startowy dla następnego agenta: cel, aktywna linia pracy, rzeczywisty stan, najważniejsze ustalenia, ograniczenia, otwarte problemy i następny krok. |

`LIFE` oznacza tutaj dziennik życia zadania/środowiska, nie nowy standard zewnętrzny.

Generuj widoki z wersjonowanych rekordów i zdarzeń. Nie utrzymuj czterech niezależnych wersji prawdy. Jawne notatki badacza/agentów są osobnymi rekordami wejściowymi; wygenerowanych widoków nie traktuj jako miejsca potajemnego zmieniania dowodów.

Nie nadpisuj istniejącego katalogu `research/` użytkownika. Zastosuj kontrolę własności/manifest generacji albo jawnie skonfigurowany, nowy katalog. Do oryginalnego projektu wejściowego nic nie zapisuj.

## 4. Trwałość i granica zaufania

Proponowany minimalny backend: SQLite z biblioteki standardowej Pythona, lokalnie po stronie kontrolera, np.:

```text
<state_dir>/history/<project_id>/
  ledger.sqlite3
  artifacts/
  exports/
```

Baza i zaakceptowane artefakty nie mogą być częścią dysku VM ani rutynowego rollbacku gościa. Nie montuj ich gościowi z prawem zapisu. Kontroler jest jedynym autorytatywnym zapisującym.

Zdarzenia są dopisywane, a korekty i odwołania są nowymi zdarzeniami. Własny hash lub łańcuch hashy nie czyni danych odpornymi na administratora hosta, który może zmienić dane i hashe. Nie obiecuj niezmienności wobec przejętego kontrolera. Zachowaj możliwość kopii na oddzielnym nośniku.

Gość może wytwarzać lokalne rekordy w katalogu outbox i artefakty w swoim dysku. Są to dane niezaufane, nie wpisy administratora. Dla MVP wystarczy odbiór po zatrzymaniu VM, przez ograniczony ekstraktor offline. Nie dodawaj nowej usługi nasłuchującej, dostępu do bazy z gościa ani zapisywalnego współdzielonego katalogu.

Wyraźnie oznacz wpisy nieodebrane jako `LOCAL_PENDING`. Zapisu, którego kontroler jeszcze nie odebrał, nie wolno przedstawiać jako odpornego na usunięcie lub cofnięcie VM. Przed planowanym rollbackiem odbierz dostępne notatki; przy niemożności odczytu zachowaj rescue i zapisz lukę. Nie obiecuj odzyskania nieprzekazanych danych z nieczytelnego dysku.

Przy dostarczaniu nowego kontekstu do gościa użyj kontrolowanego, tylko-do-odczytu pakietu/importu. Nie dopisuj kontekstu do aktywnego QCOW2 narzędziem offline. Widok wewnątrz gościa może być wygenerowany lokalnie, ale musi wskazywać ostatnią odebraną rewizję dziennika i lokalne, jeszcze niepotwierdzone zmiany.

## 5. Model rekordów

Rozróżnij:
- `project_id`: stabilna tożsamość projektu, nie sama nazwa katalogu;
- `environment_id`: środowisko VM;
- `task_id`: cel zadania;
- `run_id` / `session_id`: przebieg i sesja konkretnego agenta;
- `lineage_id`: gałąź historii wykonania, istotna przy rollbacku;
- `step_id`: logiczny krok badania/zmiany;
- `operation_id`: idempotentna operacja kontrolera.

Istniejącego `run_id` z importu nie redefiniuj po cichu. Udokumentuj zgodną migrację i dodaj brakujące identyfikatory. Zmiana modelu lub nowa sesja nie może podszywać się pod poprzednią sesję.

Minimalne zdarzenie zawiera:

```text
schema_version, event_id, project_id, environment_id,
task_id, run_id/session_id, lineage_id, step_id?, operation_id?,
event_type, received_at_utc, source_timestamp?, controller_sequence,
authenticated_actor?, reported_actor?, actor_kind,
provenance, git_commit?, branch?, checkpoint_id?,
workspace_state_ref?, evidence_refs[], supersedes?, payload
```

Czas z gościa i nazwa modelu podana przez agenta są deklaracjami. Kolejność przyjęcia ustala kontroler. Tożsamości nie nadaje pole JSON `actor_kind=human`. Nie dopisuj fikcyjnego uwierzytelnienia do odbioru offline; zapisz rzeczywiste źródło i powiązanie z kontrolowanym zadaniem/obrazem.

Wynik badania zawiera co najmniej: pytanie, założenia, metodę, opis obserwacji, ograniczenia, odniesienia do dowodów, wejścia/parametry, kod i środowisko. Dla eksperymentu zapisz seed, gdy istnieje, wersje narzędzi oraz odciski wejść/wyjść. Brakujące elementy oznacz jako nieznane, nie odtwarzaj ich z wyobraźni.

Stosuj dwie niezależne osie:

```text
execution_status: PASS | FAIL | BLOCKED | NOT_RUN
provenance: AGENT_REPORTED | GUEST_REPORTED |
            CONTROLLER_OBSERVED | INDEPENDENTLY_CHECKED | HUMAN_REVIEWED
```

Dodatkowo rodzaj twierdzenia: np. `ASSUMPTION`, `CONJECTURE`, `NUMERICAL_OBSERVATION`, `LEMMA_CANDIDATE`, `COUNTEREXAMPLE`, `DECISION`, `FORMAL_CHECK_RESULT`.

Dostarczony przez gościa log z napisem PASS pozostaje dowodem zgłoszonym przez gościa. Samo przyjęcie pliku i zweryfikowanie hasha nie zamienia go w niezależnie sprawdzony wynik. Udany test numeryczny nie jest dowodem ogólnego twierdzenia. Wynik sprawdzania formalnego identyfikuje checker, wersję, twierdzenie i założenia.

## 6. Automatyzacja i Git

Kontroler automatycznie rejestruje operacje, które rzeczywiście obserwuje: import, provisioning, define/start/shutdown/status transitions, utworzenie checkpointu, próby i wynik rollbacku, eksport oraz przekazanie sesji.

Powody decyzji i interpretację wyników podaje badacz lub agent w jawnej notatce. Nie produkuj automatycznie szczegółowego uzasadnienia z samego diffu. Brak opisu oznacz `UNANNOTATED` i wskaż go przy handoffie.

Wygodny helper wewnątrz gościa może obsługiwać krok:

```text
step.begin → notatka/zmiana/test → jawne stage → commit → step.finish
```

Nie wykonuj projektowego kodu, testów ani poleceń zapisanych w notatkach na hoście kontrolera. W MVP sterowanie eksperymentem odbywa się w VM; testy runnera Cloud są osobno oznaczone.

Przed commitem nadaj stabilny `step_id`. Opcjonalne trailery: `QSX-Task`, `QSX-Step`, `QSX-Run`. Po commicie rejestruj mapowanie `step_id → pełny SHA` w dzienniku. Nie twórz pętli: dopisanie własnego SHA do pliku → nowy commit → ponowne dopisanie SHA.

Nie wymuszaj automatycznego `git add -A`. Nie włączaj przypadkowych hooków importowanego projektu. Hook lub AGENTS.md nie jest granicą bezpieczeństwa. Wykrywaj commity spoza helpera podczas kontrolowanego odczytu historii i oznaczaj brak ich notatek; nie udawaj, że wszystkie operacje gościa są monitorowane w czasie rzeczywistym.

Nowe odgałęzienia i przepisanie historii nie mogą usuwać już zarejestrowanych faktów. Jeśli commit przestanie być osiągalny w bieżącej gałęzi, zachowaj odniesienie z właściwym statusem dostępności. Dla przenośnych eksportów zachowuj zatwierdzone artefakty/history bundle w zakresie potrzebnym do odtworzenia, a nie tylko obietnicę, że hash wskazuje na istniejące dane.

## 7. Checkpoint i rollback: chronologia nie jest pochodzeniem

Utrzymuj osobno:
1. chronologiczną sekwencję zdarzeń;
2. aktywną linię wykonania i jej checkpoint bazowy.

Nie wybieraj rodzica nowego checkpointu przez sortowanie nazw plików. Po sekwencji:

```text
CP-A → CP-B → rollback do CP-A → nowa praca → CP-C
```

historia chronologiczna pamięta CP-B, ale rodzicem CP-C jest CP-A, nie CP-B.

Do manifestu checkpointu dodaj identyfikatory projektu/zadania/linii, punkt bazowy, rewizję odebranego dziennika oraz odniesienia do odebranych dowodów. Dziennik poza VM nie podlega restore.

Rollback powinien:
- zapisać zamiar z unikalnym `operation_id`;
- zebrać dostępne końcowe dowody i zachować rescue zgodnie z polityką;
- zweryfikować i odtworzyć dysk istniejącym mechanizmem;
- odnotować sukces/błąd, checkpoint źródłowy i docelowy;
- ustanowić nową linię kontynuacji i unieważnić stary pakiet bieżącego kontekstu;
- nie kasować nieudanych eksperymentów ani poprzednich decyzji.

Po restore wyniki z porzuconej ścieżki są historyczną wiedzą, ale nie są automatycznie wynikami aktywnego kodu. MEMORY może zachować informację „ta metoda nie sprawdziła się w przebiegu R2”; CONTEXT ma jasno podać, że aktywny dysk pochodzi z CP-A.

## 8. Aktualność kontekstu i handoff

Każdy pakiet kontekstu ma manifest z `project_id`, `task_id`, aktywną linią, odczytanym HEAD, rzeczywistą gałęzią, odciskiem workspace, rewizją dziennika i wersją rendererów.

Odcisk workspace obejmuje uzgodniony zestaw treści, ścieżek, istotnych trybów plików i dowiązań. Uwzględnij niezacommitowane pliki oraz przyjęte do zakresu pliki ignorowane. Zapisz zakres jawnie. Hash tekstu `git status` nie wystarcza. Wyłącz własne wygenerowane widoki z ich samoodnoszącego się odcisku; nie wyłączaj po cichu wejść eksperymentu.

`CONTEXT.md` musi zawierać:
- aktualny cel i kryterium powodzenia;
- stan aktywny vs historyczny, HEAD/gałąź/dirty/checkpoint;
- najważniejsze ustalenia z typem, statusem i źródłami;
- nieudane podejścia oraz warunki, przy których warto je ponowić;
- otwarte kwestie, znane błędy, ograniczenia i następne kroki;
- ostrzeżenia o nieodebranych zdarzeniach, brakujących artefaktach i niezgodności stanu.

Ustaw niewielki konfigurowalny budżet kontekstu, np. 12 KiB UTF-8, z jednoznacznym raportem pominięć. To proponowany limit produktu, nie limit modelu. Nie obcinaj UTF-8 ani pojedynczych rekordów w połowie. Krytyczne ostrzeżenia mają pierwszeństwo; gdy ich nie da się zmieścić, zwróć jawny błąd zamiast ukryć ostrzeżenie. Pełny LIFE pozostaje w archiwum, a agent dostaje odnośniki do potrzebnych fragmentów.

Przy handoffie/resume:
- odczytaj aktualny stan z gościa lub wiarygodnego pakietu obserwacji, nie z nieaktualnego stagingu hosta;
- sprawdź zgodność manifestu;
- przy niezgodności wygeneruj nowy pakiet albo zwróć `STALE/BLOCKED`;
- nie zmieniaj gałęzi hostowej kopii wejściowej w celu udawania wznowienia gościa.

Dopisz do AGENTS.md procedurę czytania pakietu oraz tworzenia notatki końcowej. Wymaganie przeczytania nie dowodzi zrozumienia. Opcjonalne potwierdzenie użytej rewizji jest śladem procesu, nie gwarancją zachowania modelu. Nie wymagaj prywatnego toku rozumowania; zapisuj jawne uzasadnienia, decyzje i dowody.

## 9. Odporność zapisu i odbioru danych

Użyj transakcji, blokowania pojedynczego zapisującego, wersji schematu, kluczy idempotencji i atomowej publikacji generowanych pakietów. Zapisuj rozpoczęte operacje, zakończenia i stan wymagający uzgodnienia po awarii.

Nie twierdź, że jedna transakcja SQLite obejmuje także wymianę QCOW2, Git i pliki manifestów. Wymagany jest dziennik zamiaru i procedura recovery/reconcile dla przerwanego kroku. Nie emituj `completed`, jeśli znany jest tylko zamiar.

Błąd dziennika blokuje publikację sukcesu i rozpoczęcie nieodwracalnej operacji, która wymaga zachowania historii. Awaryjne zatrzymanie VM nie może jednak zależeć od sprawności bazy; udokumentuj późniejszy zapis luki audytowej.

Waliduj rozmiary, schematy, dozwolone typy rekordów, identyfikatory, zakres projektu i powiązania artefaktów. Bezpieczeństwo odbioru danych z gościa nie może polegać na nazwie pliku ani samoopisanej roli. Nie wykonuj hooków, filtrów, fragmentów powłoki lub instrukcji z importowanych danych. Pakiety pamięci to dane badawcze, nie uprawnienia ani instrukcje systemowe.

Nie loguj środowiska procesu, haseł, tokenów, auth.json, prywatnych kluczy, pełnych niezatwierdzonych transkryptów ani całego dysku. Stosuj jawny zakres przechowywanych danych; redakcję i ograniczenia stosuj przed trwałym zapisem. Heurystyczny skaner sekretów jest tylko dodatkową kontrolą. Polityka awaryjnego usunięcia ujawnionego sekretu wymaga operatora, rotacji i śladu redakcji — nie obiecuj bezwarunkowej nieusuwalności tajnych danych.

## 10. Proponowane CLI i miejsca integracji

Poniższe komendy są projektem API, nie komendami obecnie istniejącymi. Możesz poprawić nazwy przy zachowaniu funkcji i udokumentowaniu zmian:

```text
qsx history list <task>
qsx history collect <task>             # odbiór ograniczonego pakietu z zatrzymanego gościa
qsx history verify <task>
qsx context build <task> --output <new-dir>
qsx context check <task> --bundle <dir>
qsx handoff <task> --output <new-dir>
```

Helper notatek w VM ma oddzielnie udokumentowany tryb guest; nie otwiera bazy kontrolera. Podstawowe operacje lifecycle nadal działają jako istniejące komendy, z automatycznym rejestrowaniem rzeczywiście wykonanych zmian.

Proponowane moduły: `history.py` (schema/storage), `research.py` (rekordy), `context.py` (deterministyczne widoki), ograniczony adapter odbioru z gościa. Nie kopiuj bazy SQLite do Git jako źródła prawdy. Eksport JSONL i metadane są przenośną reprezentacją.

W kontrolowanym eksporcie dodaj `research/CHANGELOG.md`, `MEMORY.md`, `LIFE.md`, `CONTEXT.md`, `events.jsonl` i `manifest.json` oraz dopuszczone artefakty. Nie eksportuj historii innych projektów ani sekretów. Brakujące pliki dowodowe muszą być oznaczone, a nie pomijane bez śladu.

Migracja starych zadań: można odtworzyć znane identyfikatory z task.json, metadane Git i istniejące checkpointy. Każdy taki wpis oznacz `MIGRATED/RECONSTRUCTED`; brak historycznych powodów decyzji pozostaje brakiem. Nie fabrykuj dawnych sesji.

## 11. Testy odbiorcze

Przygotuj mapę test → asercja → warstwa dowodu. Testy Cloud mają wykorzystywać prawdziwą SQLite, pliki tymczasowe i Git, gdzie to możliwe.

1. Import zwykłego katalogu i repo bez commita tworzy oddzielną historię; źródło pozostaje niezmienione.
2. Cztery widoki generują się z rekordów i zachowują stabilne odnośniki; brak zależności od modelu lub sieci.
3. Dwie identyczne operacje odbioru nie dublują zdarzenia; inne zdarzenia zachowują kolejność nadaną przez kontroler.
4. Fałszywie zadeklarowana rola lub PASS z gościa nie nadaje uprawnień i nie podnosi poziomu dowodu.
5. Krok mapuje się na prawdziwy commit, bez samoodnoszącego się SHA i pętli commitowania widoków.
6. Zmiana treści już zmodyfikowanego pliku unieważnia kontekst, nawet gdy tekst git status pozostaje taki sam.
7. Nowy HEAD, zadanie, linia pracy lub rewizja pamięci unieważnia poprzedni pakiet według jawnej polityki.
8. CP-A → CP-B → rollback do CP-A → CP-C: dziennik zachowuje B, rodzicem C jest A, a kontekst wskazuje aktywną linię.
9. FAIL i wyniki negatywne zachowują się po rollbacku, ale nie są przedstawiane jako aktywne wyniki innego stanu kodu.
10. Korekta/odwołanie wpisu nie niszczy oryginału; podsumowanie wskazuje aktualny status.
11. Przerwanie po zamiarze, po zapisie pliku i przed finalizacją nie daje pozornego sukcesu; recovery nie dubluje operacji.
12. Równoległe zapisy nie uszkadzają bazy, nie tracą zdarzeń i nie podmieniają pakietu w połowie.
13. Odebrana notatka przeżywa restore; nieodebrana notatka pozostaje jawnie poza gwarancją trwałości.
14. Resume po provisioningu nie wraca do hostowego stagingu jako źródła bieżącego stanu.
15. Eksport zawiera wyłącznie wskazany projekt; sekretowe fixtures są blokowane/redagowane przed zapisem zgodnie z polityką.
16. Usunięty/modyfikowany artefakt daje niezgodność lub brak, nie status zweryfikowanego dowodu.
17. Budżet kontekstu działa deterministycznie; ostrzeżenia pozostają widoczne, pełna historia nie jest kasowana.
18. Odzyskanie bazy z JSONL/manifestu odtwarza dozwolone rekordy i ich pochodzenie; brak artefaktów jest raportowany.
19. Manifest sprzed migracji jest obsługiwany jawnie, bez dopisywania fikcyjnej historii.
20. Pomoc CLI i polecenia pamięci działają po instalacji z wheel w świeżym venv poza repozytorium.

Osobno do LOCAL_ACCEPTANCE.md: rzeczywisty import i boot gościa, notatka/test/commit wewnątrz VM, zimny odbiór, checkpoint, nowa zmiana, rollback, nowy context i eksport. Sprawdź brak dostępu gościa do bazy kontrolera. Nie oznaczaj tych sprawdzeń PASS na podstawie atrapy ekstraktora lub samych testów SQLite.

## 12. Kolejność wdrożenia i wynik

Zachowaj trzy etapy w ramach ograniczonego zakresu:
1. schemat, journal, migracja oraz rzeczywiste testy Cloud;
2. integracja z lifecycle, aktywna genealogia checkpointów i kontekst oparty o właściwe źródło;
3. widoki, handoff, export, AGENTS.md i dokumentacja odbioru.

Najpierw domknij błędy wpływające na prawdziwość historii. Nie maskuj brakującego runtime serią plików Markdown. W wyniku przedstaw: kod, małe commity, raport wykonanych testów, przykład syntetycznej historii po rollbacku, instrukcję kolejnego agenta i listę rzeczy jeszcze BLOCKED/NOT_RUN.

**Kryterium końcowe:** po zmianie agenta lub rollbacku badacz rozpoznaje rzeczywisty aktywny stan, zachowuje historię wcześniejszych podejść i może dotrzeć do konkretnych dowodów bez polegania na pamięci jednej rozmowy.

## 13. Źródła i pochodzenie specyfikacji

Wymagania projektowe wynikają z rozmowy operatora. Ustalenia o obecnym kodzie pochodzą z odczytu plików wskazanego commita; nie stanowią pełnego audytu ani uruchomienia projektu.

```text
PR: https://github.com/niirmataa/qsx-sandbox/pull/1
Baza plików: https://github.com/niirmataa/qsx-sandbox/tree/a2954f6e9560dbd4afc6e1eb0fe818ad4c782853
Odczytano: src/qsx/cli.py, workspace.py, disk.py, provision.py,
          AGENTS.md, reports/CLOUD_VALIDATION.md oraz drzewo repozytorium.
Git hooks: https://git-scm.com/docs/githooks
Git commit/trailers: https://git-scm.com/docs/git-commit
SQLite atomic commit: https://sqlite.org/atomiccommit.html
Instrukcje Codex: https://developers.openai.com/codex/guides/agents-md
  przekierowanie: https://learn.chatgpt.com/docs/agent-configuration/agents-md
```
