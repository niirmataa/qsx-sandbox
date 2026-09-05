# QSX — pakiet startowy zadania Codex Cloud

To specyfikacja, instrukcje agenta i skrypty przygotowania narzędzi developerskich.
Nie zawiera zaimplementowanego QSX ani obrazu VM. Nie utworzono środowiska w Twoim
koncie i nie wykonano instalacji na docelowym laptopie.

## Użycie

Umieść zawartość tego katalogu w głównym katalogu osobnego, prywatnego repozytorium
infrastruktury QSX i zatwierdź pliki. Nie trzeba przenosić repo FT1536. W Codex
Cloud wybierz to repo i gałąź zawierającą pakiet; nie tylko załącznik z rozmowy.
W istniejącym repo połącz AGENTS.md i .gitignore, zamiast nadpisywać je bez przeglądu.

Codex pobiera wybraną rewizję repo i wykonuje konfigurację środowiska [C1].
Instrukcje repozytorium umieszczamy w AGENTS.md [C3].

## Ustawienia środowiska — propozycja QSX

| Pole | Wartość |
|---|---|
| Nazwa | QSX-Builder |
| Repo/rewizja | Prywatne repo QSX, branch lub SHA zawierający ten pakiet |
| Obraz | Standardowy `universal`; zweryfikuj Linux z apt-get |
| Runtime | Python 3.12 jako wersja referencyjna |
| Zmienna środowiskowa | `QSX_BUILD_CONTEXT=codex-cloud` |
| Setup script | `bash codex/setup.sh` |
| Maintenance script | `bash codex/maintenance.sh` |
| Agent internet access | Off na start |
| Secrets | Puste dla tego zadania |

Setup korzysta z sieci do zależności; zmienne z samego `export` w setup nie
przechodzą do sesji agenta [C1]. Dlatego znacznik kontekstu ustaw w panelu,
a narzędzia Python uruchamiaj przez `.venv/bin/python`.

Skrypt instaluje pakiety w runnerze, więc jego treść wymaga przeglądu. Nie wykonuj
go na laptopie produkcyjnym. Potrzebne są standardowe uprawnienia instalacji
pakietów w środowisku; ich brak ma zakończyć setup błędem, nie próbą obejścia.
Nie instalujemy demona libvirt, nie uruchamiamy sieci VM i nie otwieramy portów.

Pierwszy setup jest bootstrapem bez przypiętych narzędzi Python: ich wersje
zostają zapisane. Agent ma potem dostarczyć kompletny lockfile z hashami i
sprawdzić powtarzalny setup. Skrypt użyje locka, gdy będzie już w repo.
Nie twierdzimy, że sam manifest wersji zapewnia odtwarzalność.

Po zmianie zależności powtórz setup/reset cache przed kolejnym zadaniem.
Maintenance odmawia pracy przy innym hashu lockfile.

## Dostęp do dokumentacji

Domyślnie agent pozostaje bez internetu. Gdy potrzebuje aktualnej dokumentacji,
możesz dopuścić tylko wymagane domeny dokumentacyjne i metody GET/HEAD, zamiast
opcji All/Common dependencies [C2]. Kandydaci: qemu.org, www.qemu.org, libvirt.org,
git-scm.com, developers.openai.com, learn.chatgpt.com. Sprawdź faktyczne
przekierowania; nie rozszerzaj listy na cały internet. Ograniczenie metod HTTP
nie jest gwarancją, że żadne dane nie mogą zostać wysłane w zapytaniu.

Polityka internetowa runnera nie jest dowodem działania przyszłej polityki
`offline/openai` w MathResearch. To dwa osobne środowiska.

## Zadanie

Wklej tekst z START_TASK.txt. Pierwszym celem jest implementacja i testy w chmurze;
końcowa konfiguracja sprzętu oraz odbiór bezpieczeństwa pozostają lokalne.

## Co zostało sprawdzone w tym pakiecie

Patrz PREPARATION_CHECKS.md. Skrypty setup/maintenance nie zostały uruchomione
w Twoim Codex Cloud. Gotowość środowiska trzeba potwierdzić w jego logu setup.

## Pliki

- `docs/QSX_MVP_AGENT_TASK.md`: pełna specyfikacja produktu z trybem CLOUD_BUILD.
- `docs/CODEX_CLOUD.md`: macierz testów i rozdział budowy od lokalnego odbioru.
- `AGENTS.md`: krótkie instrukcje obowiązujące przy implementacji.
- `codex/setup.sh`, `codex/maintenance.sh`, `codex/capabilities.py`: przygotowanie
  narzędzi i odczytujący raport runnera, nie instalator QSX.
- `START_TASK.txt`: polecenie dla agenta.

[C1] `https://developers.openai.com/codex/cloud/environments`

[C2] `https://developers.openai.com/codex/cloud/internet-access`

[C3] `https://developers.openai.com/codex/guides/agents-md/`
