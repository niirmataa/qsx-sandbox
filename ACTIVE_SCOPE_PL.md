# Aktywny zakres — Alpine task images

Gałąź wykonawcza: `feat/alpine-task-images`.
Punkt wejścia: commit `88abbbb451e63e3179f87a88192c7e95042333bd`,
tree `fb96d712f2f4b2bac9c4f647c6e60dab4a92abaa`.

Najnowsza instrukcja operatora zastępuje wcześniejszy zakres z dokumentów:

> Dostarcz samodzielne narzędzie budowania i uruchamiania sandboxów Alpine
> z receptur zadania i profilu agenta. Nie rozwijaj teraz Research Memory
> ani nie aktualizuj PR #1. Zachowaj istniejące dokumenty; starsze specyfikacje
> traktuj jako tło historyczne, nie zakres tej implementacji.

Rdzeń QSX pozostaje na `archive/qsx-core-pr1-3eb4908`, commit
`3eb49087ee6aba4423fbb26b02f9bff11ad241b7`. Nie jest zależnością pakietu
`qsx-alpine`. Dotychczasowe dokumenty i historia pozostają zachowane.

Cel: małe warsztaty Alpine x86_64, odtwarzalne wejścia, dowolne receptury zadań
i osobne profile agentów, prywatne instancje oraz lokalna konsola.
Hosty dopuszczone: Ubuntu 24.04 LTS i Debian 13 z QEMU/KVM i libvirt.
Bieżący odbiór lokalny odbywa się na Ubuntu 24.04 LTS. Uprawnienia i zasoby ogranicza
osobna polityka operatora. Domyślna sieć jest odcięta.

Praca w Work dostarcza kod, podpisane wejścia, testy dostępne w runnerze oraz
pakiet przekazania. Operator z Astrą ULTRA w Codex CLI kończy rzeczywistą
budowę/rozruch i odbiór Debiana. Nie ogłaszamy rozruchu, działania KVM,
egzekwowania sieci ani zalogowanej pracy modelu bez wykonania tych testów.

Poza zakresem tej implementacji: rozwój Research Memory, integracja starego
CLI, aktualizacja PR #1, merge, panel, klaster i MCP. Narzędzia kryptograficzne
można dobierać w recepturach; SageMath nie jest obowiązkowym składnikiem bazy.

Doprecyzowanie operatora: przydatny jest osobny warsztat, w którym SageMath
i Lean 4 są podstawą. Dodajemy taką recepturę źródłową obok lekkich przykładów.
Nie powiększa ona każdego obrazu. Jej ciężka kompilacja i odbiór na Alpine
pozostają lokalne; sam plik receptury nie oznacza zaliczonej integracji tych
narzędzi. Pozostałe zestawy i właściwe badania powstaną później.

Operator dopuścił wariant zapasowy: gdy natywna kompilacja narzędzi na Alpine
będzie na tym etapie przeszkodą, osobna cięższa receptura może uruchamiać je
w Dockerze wewnątrz VM Alpine. Ten wariant nie jest jeszcze zaimplementowany.

Priorytetem jest działający, odtwarzalny warsztat z właściwymi narzędziami.
Rozmiar obrazu jest pomiarem zależnym od zadania, a nie powodem rezygnacji
z potrzebnego SageMath, Lean lub ich zależności.

## Lokalna kontynuacja

Aktywne instrukcje operatora znajdują się w `AGENTS.md`. Dokładną kopię
starszych instrukcji Codex Cloud zachowano w
`docs/history/AGENTS_CODEX_CLOUD.md`; jest materiałem historycznym, nie
zakresem ani polityką bieżącego lokalnego wykonania.
