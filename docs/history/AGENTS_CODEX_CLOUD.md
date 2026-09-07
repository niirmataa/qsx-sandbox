# QSX — instrukcje dla Codex Cloud

Przeczytaj `docs/QSX_MVP_AGENT_TASK.md` i `docs/CODEX_CLOUD.md` przed implementacją.
Pracujesz nad kodem infrastruktury, nie na docelowym laptopie. Wyniki muszą
odróżniać implementację, test symulowany, rzeczywistą integrację i lokalny odbiór.

## Granice

- Nie próbuj łączyć się z serwerem właściciela. Nie potrzebujesz jego repo FT1536,
  tokenów OpenAI, kluczy SSH/WireGuard ani hostowej konfiguracji.
- Nie uruchamiaj instalatora hosta, nie zmieniaj sieci runnera ani jego uprawnień.
  Nie obchodź ograniczeń KVM, urządzeń, namespace'ów ani usług platformy.
- Właściwy QSX ma egzekwować ograniczenia poza gościem; instrukcje, hooki Git
  i zmienne środowiskowe nie są granicą bezpieczeństwa.
- Zaimplementuj prawdziwy adapter libvirt; mocki nie zastępują produktu.
- Nie wykonuj kodu importowanych projektów na hoście kontrolera. Dane testowe
  są syntetyczne. Zależności pobieraj w kontrolowanej fazie setup.

## Praca i testy

1. Wykonaj `python3 codex/capabilities.py`; nie odczytuj całego `env`.
2. Używaj `.venv/bin/python`, bez założenia, że aktywacja venv z setup się utrzyma.
3. Zaimplementuj `make test-cloud`: unit + real Git + offline qemu-img +
   schematy/kontrakty adaptera. TCG może być dodatkowe; KVM i izolacja hosta
   mają osobny lokalny odbiór.
4. Wykonuj logiczne zmiany i commity na roboczej gałęzi zadania. Nie zmieniaj
   źródłowego main ani zdalnych ustawień repo. Nie wykonuj force-push.
5. Nie wstawiaj TODO jako działającej funkcji, nie ignoruj błędów i nie zaliczaj
   testu tylko dlatego, że mock zwrócił oczekiwany tekst.
6. Raportuj PASS/FAIL/BLOCKED/NOT_RUN z warstwą dowodu i komendą. Błąd kodu
   jest FAIL, a brak sprzętu może być BLOCKED tylko dla odpowiedniego testu.
7. Przygotuj lockfile z hashami i sprawdź czyste odtworzenie zależności przed
   ogłoszeniem CLOUD_VALIDATED. Bootstrap bez locka to nie build odtwarzalny.

## Wynik

Kod + testy + raport + instrukcja lokalnego odbioru. Implementuj teraz jedną VM,
bez MCP, klastra i zdalnego pulpitu. Plan dalszej infrastruktury pozostaje w
specyfikacji. Status HOST_VALIDATED wymaga późniejszego testu na laptopie.
