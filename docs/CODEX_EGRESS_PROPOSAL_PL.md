# Codex 0.153.4 — konkretna propozycja wyjścia dla jednej instancji

Stan 2026-09-07: konfiguracja przygotowana do przeglądu, **NIE WDROŻONA**.
Operator nie zatwierdził jeszcze hostowej sieci, usługi ani domen.
Moduł `qsx_alpine.egress` jest osobnym elementem transportu; sam proces proxy
nie stanowi egzekwowanej granicy VM. Zintegrowane uruchomienie runtime,
testy VM allow/deny i odcięcie aktywnego połączenia są jeszcze wymagane.
Istniejące polityki/receptury pozostają offline i start z obcym NIC jest odrzucany.

## Zakres decyzji operatora

Proponowany wariant ChatGPT: dokładne `auth.openai.com:443` i `chatgpt.com:443`.
Jedna prywatna instancja Alpine otrzyma jeden interfejs w nowej izolowanej sieci
libvirt `qsx-codex-egress`, bridge `qsx-egress0`, adres gościa `192.168.250.2/30`
i hosta `192.168.250.1/30`. Na tym hoście trasy sprawdzone 2026-09-07 nie kolidują
z podsiecią (Wi-Fi `172.20.10.0/28`); powtórzyć kontrolę przed wdrożeniem.
Brak NAT, forward, DHCP, DNS i autostartu sieci. Osobna usługa bez uprawnień roota
nasłuchuje tylko na `192.168.250.1:3128`. Osobne tabele nftables inet/bridge
odrzucają pozostały ruch do hosta, forwarding, IPv6 i ruch między VM.
Nie zmieniamy AppArmor, seccomp, globalnego IP forwarding ani istniejących tabel.

Proxy akceptuje wyłącznie HTTP CONNECT na port 443 dla dokładnie wymienionych
hostów, wymaga takiego samego jawnego SNI w TLS ClientHello, odrzuca ECH, adresy
IP zamiast nazw oraz odpowiedzi DNS zawierające adres niepubliczny. Łączy się
z zatwierdzonym wynikiem DNS bez ponownego rozwiązania nazwy. Nie odszyfrowuje
TLS i nie zapisuje tokenów, nagłówków HTTP, treści ani kodów logowania.
Log zawiera tylko stały typ zdarzenia i ewentualną dozwoloną nazwę domeny.
To allowlista miejsca docelowego i jawnego SNI: nie filtruje ścieżek HTTP ani
treści wysyłanych do dozwolonych usług, nie gwarantuje ochrony przed domain
fronting obsługiwanym wewnątrz dozwolonego serwisu. Serwerowy certyfikat nadal
weryfikuje klient. Ograniczenie hostowe pozostaje egzekwowane poza gościem także
po zmianie zmiennych proxy przez agenta.

Do zatwierdzenia: instalacja `/opt/qsx-egress`, `/etc/qsx-egress`, jednostki
`qsx-egress-proxy.service`, root-owned helpera `/usr/local/sbin/qsx-egress-verify`
i reguły sudoers pozwalającej operatorowi `god` wyłącznie na jego odczytowe
wywołanie bez argumentów, dwóch nowych tabel nftables, jednej sieci libvirt
i jednego NIC wybranej instancji; publiczne wyjście proxy do dwóch nazw powyżej.
`api.openai.com` nie jest w tej propozycji dozwolone. Wariant API wymaga osobnej
decyzji o endpointach i rozliczeniu. Wybór modelu jest niezależny od transportu.

## Pliki i polecenia przygotowane do wdrożenia po zgodzie

Pliki w `deploy/egress/`: `proxy.toml`, `network.xml`, `firewall.nft`,
`qsx-egress-proxy.service`, `qsx-egress-verify`, `sudoers.example`. Nie są wczytywane przez zwykły start QSX.
Poniższy etap hostowy jest gotowy do przeglądu; **nie uruchamiać go przed zgodą**
ani przed zakończeniem testów modułu i integracji runtime.

```sh
cd /home/god/Muzyka/QSX-ALPINE-SANBOX-v1/qsx-delivery/qsx-alpine
.venv/bin/python -m qsx_alpine.egress --config deploy/egress/proxy.toml --check
ip -4 route show
virsh --connect qemu:///system net-list --all
sudo nft -c -f deploy/egress/firewall.nft
sudo nft list ruleset
```

Sprawdzić, że nazwy sieci, tabel i katalogi nie istnieją; nie nadpisywać cudzych
zasobów. Obecność przygotowanych wcześniej zasobów wymaga porównania zawartości.
Dokładny wheel ma powstać przez `make check` po testach i przeglądzie.

```sh
sudo install -d -m 0755 /opt/qsx-egress /etc/qsx-egress
sudo python3 -m venv /opt/qsx-egress
sudo /opt/qsx-egress/bin/python -m pip install --no-index --no-deps dist/qsx_alpine-0.1.0-py3-none-any.whl
sudo install -m 0644 deploy/egress/proxy.toml /etc/qsx-egress/proxy.toml
sudo install -m 0644 deploy/egress/firewall.nft /etc/qsx-egress/firewall.nft
sudo install -m 0644 deploy/egress/qsx-egress-proxy.service /etc/systemd/system/qsx-egress-proxy.service
sudo install -m 0755 deploy/egress/qsx-egress-verify /usr/local/sbin/qsx-egress-verify
sudo visudo -cf deploy/egress/sudoers.example
sudo install -m 0440 deploy/egress/sudoers.example /etc/sudoers.d/qsx-egress-verify
sudo /opt/qsx-egress/bin/python -I -m qsx_alpine.egress_verify --seal
sudo nft -f /etc/qsx-egress/firewall.nft
virsh --connect qemu:///system net-define deploy/egress/network.xml
virsh --connect qemu:///system net-start qsx-codex-egress
sudo systemctl daemon-reload
sudo systemctl start qsx-egress-proxy.service
sudo systemctl status --no-pager qsx-egress-proxy.service
sudo nft list table inet qsx_codex_egress
sudo nft list table bridge qsx_codex_egress
virsh --connect qemu:///system net-dumpxml qsx-codex-egress
sudo -n /usr/local/sbin/qsx-egress-verify
```

Verifier wymaga root-owned plików/katalogów bez zapisu dla grupy/innych, manifestu
SHA256 instalacji `/etc/qsx-egress/deployment.json`, dokładnego cmdline procesu
usługi, UID różnego od 0, właściciela właściwego socketa LISTEN i obu tabel
zgodnych z przygotowanym plikiem. Wrapper używa izolowanego Pythona `-I`,
stałych ścieżek i odrzuca argumenty. Nie udziela uprawnień do zmian sieci,
wykonywania poleceń z receptur ani do buildera. Zmiana instalacji wymaga nowego
przeglądu i manifestu; `--seal` odmawia nadpisania starego manifestu. Zachować
stary manifest przed nową autoryzowaną instalacją. Odczyt tabel jest snapshotem;
root/operator pozostaje zaufaną stroną polityki hosta.

Komendy `network-prepare` i `network-grant` są zaimplementowane i objęte
lokalnymi testami kontraktowymi; ich odbiór w działającej VM jest `NOT_RUN`.
Wdrożenie hostowe pozostaje `BLOCKED` do decyzji operatora. Oddzielny przykład
polityki jest w `deploy/egress/instance-policy.example.toml`: operator wpisuje
`instance_id` konkretnej prywatnej instancji z jej `instance.json`; pozostałe
pola wskazują dokładnie tę sieć, podsieć, proxy i dwie dozwolone domeny.
Politykę zapisać poza repo i poza importowanym projektem. Nie jest polityką
zasobów ani polem receptury obrazu. Sekwencja po zatwierdzeniu wdrożenia:

```sh
qsx-alpine network-prepare --help
qsx-alpine network-grant --help
qsx-alpine status ŚCIEŻKA_INSTANCJI
qsx-alpine stop ŚCIEŻKA_INSTANCJI
qsx-alpine network-prepare ŚCIEŻKA_INSTANCJI --policy ŚCIEŻKA_POLITYKI_INSTANCJI
```

Instancja ma mieć zakończony `finalize-import` i być `SHUTOFF`. `stop` wykonuje
się tylko, jeśli wcześniejszy `status` stwierdzi działającą VM. Przygotowanie
zwraca dokładną ścieżkę nowego nośnika `egress-NONCE.iso`. Przed startem należy
sprawdzić odczyt tego pliku przez konto QEMU i przyznać wyłącznie zatwierdzony
w runbooku dostęp do tego nośnika/ścieżki. Nie wykonywać szerokich zmian ACL.
Następnie:

```sh
qsx-alpine start ŚCIEŻKA_INSTANCJI
qsx-alpine network-grant ŚCIEŻKA_INSTANCJI --policy ŚCIEŻKA_POLITYKI_INSTANCJI
qsx-alpine start ŚCIEŻKA_INSTANCJI
qsx-alpine status ŚCIEŻKA_INSTANCJI
```

Pierwszy start w tej sekwencji nadal nie ma NIC: gość importuje publiczną
konfigurację przypisaną do swojego ID i losowego MAC. Root-owned
`/etc/profile.d/qsx-egress.sh` ustawia `HTTP_PROXY`/`HTTPS_PROXY` na
`http://192.168.250.1:3128`; konfiguracja root ustawia statyczne IP bez gateway/DNS
wyłącznie dla przypisanego MAC. `network-grant` sprawdza hostowy verifier,
aktualne/trwałe XML sieci, brak drugiego klienta i marker importu przez konsolę
nie-root. Wykonuje ACPI shutdown, odłącza ISO, dodaje jeden trwały NIC i zapisuje
hash wdrożenia oraz boot ID hosta. Ostatni `start` ponownie sprawdza te warunki
przed uruchomieniem z dostępem. Błąd zamyka dostęp; stan/logi przed wznowieniem
trzeba odczytać. Timeout nie uprawnia do ślepego powtórzenia komend.

Konfiguracja/sekrety instancji nie trafiają do wzorca. `network-cut` najpierw
zatrzaskuje offline w metadanych, opuszcza aktywny link i usuwa trwały NIC;
restart nie udziela ponownie dostępu. Konsola szeregowa pozostaje dostępna.
Późniejsze ponowne nadanie wymaga jawnej nowej sekwencji prepare/grant z polityką.
Nie stosować ręcznego `attach-interface` lub startu omijającego kontroler.

Wycofanie najpierw odcina NIC wybranej instancji istniejącym
`qsx-alpine network-cut ŚCIEŻKA_INSTANCJI` i weryfikuje XML bieżący/trwały.
Dopiero bez aktywnych klientów:

```sh
sudo systemctl stop qsx-egress-proxy.service
virsh --connect qemu:///system net-destroy qsx-codex-egress
virsh --connect qemu:///system net-undefine qsx-codex-egress
sudo nft delete table inet qsx_codex_egress
sudo nft delete table bridge qsx_codex_egress
```

Nie usuwać logów i dowodów. Nie usuwać tabel, gdy NIC nadal jest aktywny.
Po restarcie hosta nie ma automatycznego nadania sieci: ponownie kontrola i
załadowanie reguł, sieci i procesu; jawne udzielenie dla instancji jest oddzielne.

## Przypięty klient, sandbox i logowanie

Wersja `0.153.4`, release `rust-v0.153.4`, commit upstream
`3d2ee51ca2d5db578f328aa75e20aa22c0197c9a`. Odczytane pliki i ich SHA256 zachowano
poza repo w `../../qsx-local-evidence/codex-egress/source-manifest.json`
(względem katalogu repo). Publiczny pakiet Codex ma SHA256
`a822187e1a2420c61c5926721bfbd878701ed95547c9bb0d4de4498a16ba1821`.

Źródła tej wersji potwierdzają device auth pod `auth.openai.com`:
`/api/accounts/deviceauth/usercode`, `/api/accounts/deviceauth/token`,
`/oauth/token`, oraz adres wyświetlany operatorowi `/codex/device`.
Domyślny endpoint sesji ChatGPT to `https://chatgpt.com/backend-api/codex`;
API używa `https://api.openai.com/v1`. To wniosek z odczytanego kodu przypiętej
wersji, nie twierdzenie, że już wykonano logowanie przez te dwa hosty.
Możliwy dodatkowy endpoint/redirect należy najpierw udokumentować i uzgodnić,
nie rozszerzać reguł automatycznie. Źródła: `codex-rs/login/src/device_code_auth.rs`,
`codex-rs/login/src/server.rs`, `codex-rs/model-provider-info/src/lib.rs`.

Istniejący profil `agent-profiles/codex.toml` zawiera rzeczywisty test sandboxu:
nie-root, dozwolony zapis `/workspace/project`, odmowa zapisu w zwykłym
agent-owned pliku `/home/agent/.qsx-outside-probe`. Test wykonać w zbudowanym
gościu, zachowując `workspace-write`, `on-request` i `network_access=false`
dla narzędzi. Proces klienta łączący się z modelem jest odrębny od sandboxu
uruchamianych przez niego narzędzi. Nie stosować `--yolo` ani bypass.

Po testach transportu operator otwiera `qsx-alpine console ŚCIEŻKA_INSTANCJI`
i osobiście, bez zapisu surowej sesji do raportu, wykonuje jako `agent`:

```sh
cd /workspace/project
id
codex login --help
codex login --device-auth
codex login status
```

Kod jednorazowy operator wpisuje w swojej przeglądarce pod adresem pokazanym
przez klienta. Nie kopiować istniejącego auth cache hosta. Device login może
wymagać włączenia w ustawieniach konta/workspace. To wspierana ścieżka
[oficjalnej dokumentacji uwierzytelnienia](https://learn.chatgpt.com/docs/auth)
oraz odczytanej wersji CLI; dokumentowany fallback kopiowania auth cache jest
w tym zadaniu zabroniony. Nie wykonywaliśmy logowania ani odczytu poświadczeń.

Po logowaniu małe zadanie modelu w syntetycznym repo: stworzyć plik Python
liczący `sum(i*i for i in range(1, 11))`, uruchomić go i sprawdzić `385`.
Zachować wynik oraz status testu bez tokenów/logów auth. Model wybrać jawnie
przed uruchomieniem; aktualny wybór modelu kontrolera nie zmienia profilu gościa.

## Odbiór wymagany przed ogłoszeniem działania

| Sprawdzenie | Dowód |
|---|---|
| HTTP CONNECT + TLS z poprawnym SNI | Realny handshake przez proxy, certyfikat sprawdzony przez klienta; 401/403 HTTP bez loginu dowodzi tylko transportu |
| Niedozwolona domena i port | CONNECT `example.com:443`, `auth.openai.com:80`, IP:443 odrzucone przed połączeniem upstream |
| Bypass domeny | CONNECT do dozwolonej domeny + inne SNI, brak SNI, ECH, zły/fragmentowany ClientHello; niedozwolony nie otwiera upstream |
| DNS rebinding / prywatny adres | Odmowa całej odpowiedzi DNS zawierającej loopback, LAN, link-local, multicast lub IP mapowane na prywatne IPv4 |
| Bypass proxy z gościa | Bez proxy TCP do publicznego IP:443, LAN, hosta:22/53, UDP:53/443, IPv6 i drugi VM odrzucone poza gościem; sprawdzić liczniki firewall |
| Lokalny sandbox Codex | Istniejący readiness profilu wykonany jako `agent` w gościu; zapis w workspace, odmowa poza nim |
| Aktywne odcięcie | Najpierw utrzymane dozwolone połączenie TLS; potem `network-cut`, brak dalszego przepływu, brak ponownego połączenia; konsola wykonuje `id` |
| Restart po odcięciu | Brak NIC w trwałym XML, metadane offline, brak ponownego grant |
| Sesja modelu | Osobisty login i poprawne zadanie `385`; logowanie `AWAITING_OPERATOR_LOGIN` dopiero po przygotowaniu transportu |

Własne testy modułów proxy/verifier: **64 PASS** (Python 3.12, 2026-09-07),
Ruff PASS i mypy PASS. Wykonano realny CONNECT/TLS/echo i przerwanie aktywnego
lokalnego połączenia, testy odrzucania źródła/SNI/ECH, prywatnego DNS, parsera,
manifestu i argumentów helpera. Zdalny peer TLS był kontrolowanym serwerem
loopback testu, bez połączenia z OpenAI i bez poświadczeń. Verifier live hosta
oraz reguły nftables nie były uruchomione jako wdrożenie.

Dokumentacja/protokół lokalny nie zaliczają odbioru VM. Każdy test zapisuje commit,
stan drzewa, host, polecenie, exit code i log; logowanie pozostaje poza capture.
Host rozpoznany: Ubuntu 24.04.4 LTS, kernel `6.8.0-139-generic`; Debian 13 `NOT_RUN`.
