# Przekazanie do Astry ULTRA — Debian 13

## Obowiązujący zakres

Kontynuuj na `feat/alpine-task-images`. Najpierw przeczytaj `ACTIVE_SCOPE_PL.md`,
`templates/README.md` i `docs/DEBIAN_RUNBOOK_PL.md`.
Dokumenty starego QSX są zachowane jako historia. Rdzeń na
`archive/qsx-core-pr1-3eb4908` nie jest zależnością nowego narzędzia.
Nie rozwijaj Research Memory, nie aktualizuj PR #1, nie wykonuj merge, nie dodawaj panelu,
klastra ani MCP. Nie usuwaj niezacommitowanej pracy ani istniejących dokumentów.

Budujemy samodzielny pakiet `qsx-alpine`: receptura zadania + profil agenta →
zamrożone wejścia → obraz Alpine → prywatna instancja → lokalna konsola.
Dodanie szablonu nie wymaga zmiany kodu. Politykę hosta i limity zasobów
ustawia operator osobno. Bazowy dostęp IP jest odcięty.

## Co zrobiono w Work

Implementacja znajduje się w `src/qsx_alpine`; testy w `tests`.
Pakiet nie importuje starego `qsx`. Dostarczono podpisane wejścia Alpine,
pakiety APK, publiczne klucze i dowody podpisów komponentów oficjalnego Codexa.
Dokładny commit, tree, sumy plików i wyniki testów znajdują się w raporcie
dostawy obok archiwów. Nie interpretuj starszego raportu jako wyniku nowego kodu.

Sprawdzono dostępne w runnerze operacje na rzeczywistych nieaktywnych QCOW2,
ISO, podpisach i cache oraz transport na lokalnych pseudoterminalach z
syntetycznym partnerem. To nie dowodzi rozruchu ani izolacji działającej VM.
Obrazy robocze powstaną u operatora. Nie ma zaliczonej sesji zalogowanego modelu.

## Kolejność lokalnej kontynuacji

1. Zweryfikuj sumy archiwów i Git bundle; sprawdź branch, HEAD, tree i `git status`.
   Otwórz pakiet w nowym katalogu. Nie nadpisuj istniejącego repo operatora.
2. Uruchom `qsx-alpine doctor`. Sprawdź faktyczny Debian 13, sprzęt KVM,
   libvirt, wolne miejsce, użytkownika QEMU i dostęp do prywatnych ścieżek.
3. Przed zmianą pakietów hosta, usług i ACL przedstaw operatorowi konkretny plan.
   Nie wyłączaj AppArmor/seccomp i nie stosuj szerokiego `chmod 777`.
4. Wykonaj `make check` w przygotowanym venv. Napraw błędy; nie zamieniaj ich
   w pominięte testy ani pozorowane sukcesy.
5. Użyj dołączonego cache. Nowy lock jest wymagany po zmianie kodu buildera,
   receptury lub profilu. Brakujący składnik jest błędem, nie zgodą na `latest`.
6. Uruchom `scripts/demo_debian.py --help` i jego tryb planu. Następnie wykonaj
   rzeczywistą demonstrację zgodnie z runbookiem: pierwszy build, cache build
   do nowego pliku, drugi szablon, dwie instancje, import, konsola, nie-root,
   niezależność zapisów oraz odcięcie sieci bez utraty konsoli.
7. Sprawdź faktyczny rozmiar partycji i filesystemu, działanie autologowania
   lokalnej konsoli jako `agent`, czysty shutdown ACPI i brak hostowych mountów.
   Własne błędy integracji napraw tutaj, z testem regresyjnym.
   Osobno dokończ warsztat SageMath + Lean 4 według
   `docs/SAGE_LEAN_TEMPLATE_PL.md`. Jest recepturą eksperymentalną do kompilacji
   ze źródeł, nie gotowym obrazem ani zaliczonym buildem. Zweryfikuj przypięte
   wejścia, dobierz jawnie politykę zasobów i dłuższy timeout. Nie zamieniaj
   Alpine na inną dystrybucję ani nie pobieraj zależności w fazie offline.
8. Uruchom profil Codex w gościu. Test jego lokalnego sandboxu nie zastępuje
   zalogowanej pracy modelu. Nie stosuj automatycznego obejścia ograniczeń klienta.
9. Obecny runner nie implementuje zatwierdzonego transportu wyjścia do OpenAI.
   Nie dodawaj zwykłego NAT-u i nie nazywaj go allowlistą. Zalogowana sesja
   pozostaje `AWAITING_OPERATOR_LOGIN` do zatwierdzenia i sprawdzenia odpowiedniego
   transportu oraz osobistego logowania operatora w gościu.
10. Zapisz pomiary, logi, nowe manifesty i status każdego sprawdzenia.
    Wykonaj logiczne commity na gałęzi roboczej. Dostarcz obrazy i aktualny bundle
    rzeczywistym mechanizmem przekazania. Nie wykonuj merge.

## Rozdzielenie wyników

- `PASS`: tylko dokładnie wykonany test, z poleceniem i zakresem dowodu.
- `FAIL`: błąd kodu lub integracji; wymaga naprawy.
- `BLOCKED`: konkretna brakująca możliwość/uprawnienie dla wskazanego testu.
- `NOT_RUN`: test jeszcze niewykonany.
- `AWAITING_OPERATOR_LOGIN`: brak osobistego logowania do OpenAI; nie kopiuj
  poświadczeń Work, hosta ani innych kont do obrazu wzorcowego.

Docelowy efekt to działający mały warsztat dopasowany do zadania. Profile z
narzędziami kryptograficznymi mogą powstawać jako kolejne dane wejściowe;
osobny wariant SageMath + Lean 4 jest pierwszym cięższym warsztatem wskazanym
przez operatora. Nie trzeba umieszczać go w każdym obrazie. Właściwe badania
i dalsze zestawy narzędzi powstaną później.
