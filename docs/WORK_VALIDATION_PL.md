# Wynik Work — kod i wejścia, bez odbioru VM

Sprawdzona implementacja: commit
`d477bd5c8b9cea61a903ccd4a6818858185c7d2d`, tree
`f012736d04dc710ea5192d857fb9f812a95303d5`.
Późniejszy commit dokumentacyjny nie zmienia sprawdzonych modułów.
Końcowe HEAD, tree i sumy dostawy zawiera `DELIVERY_MANIFEST.json` w pakiecie.

Wynik: **kod oraz trzy komplety wejść zweryfikowane w Work**.
Nie dostarczamy własnego zbudowanego i uruchomionego obrazu QSX. Dostarczony
QCOW2 jest podpisanym wejściem Alpine do lokalnej budowy. Operator z Astrą
kończy kompilację, rozruch i odbiór Debiana 13.

## Wykonane sprawdzenia

| Sprawdzenie | Wynik | Zakres dowodu |
|---|---|---|
| `make check` | PASS | 115 testów, 0 pominiętych, Ruff, strict mypy, build wheela i instalacja poza drzewem źródeł |
| Dyski i ISO | PASS | Rzeczywiste operacje qemu-img/xorriso na nieaktywnych dyskach; niezależne kopie i niezmienione źródło |
| Konsola i adapter | PASS | Rzeczywiste lokalne PTY i procesy; partner hypervisora syntetyczny, bez VM |
| Demo Debiana | PASS | Plan bez skutków ubocznych i kontrakty CLI, w tym kontynuacja częściowego przebiegu; nie rozruch |
| Wszystkie 5 receptur | PASS | TOML, dziedziczenie profili, składnia wygenerowanych skryptów `sh -n` |
| Podpisy wejść | PASS | GPG Alpine cloud/minirootfs, podpisy APK, Sigstore trzech komponentów ELF Codexa |
| Cache trzech receptur | PASS | Faktyczne zamrożone pliki; replay z zabronionymi wywołaniami sieci i procesów |
| Narzędzia Python 3.12 | PASS | Czysty venv, instalacja offline z hashami, kontrola zależności i wykonanie narzędzi |
| Narzędzia Python 3.13 | PASS / NOT_RUN | Rozwiązanie zależności i hashe PASS; wykonanie interpretera/testów 3.13 NOT_RUN |
| Istniejące pliki repo | PASS | 10 plików bez zmian bajtów; cały stary README zachowany po nowym wstępie; reguły ignore zachowane |

Polecenie `make check` trwało 10,067 s; same testy 5,64 s. Wynik dotyczy
runnera Ubuntu 24.04 x86_64 z Pythonem 3.12.13, QEMU 8.2.2, xorriso 1.5.6,
GPG 2.4.4 i Cosign 3.1.3. Narzędzia QEMU/xorriso/Cosign uruchamiano z lokalnego,
zweryfikowanego zestawu; nie instalowano usług ani pakietów hosta.
Proces testów działał jako root w runnerze: nie jest to dowód uprawnień
zwykłego użytkownika na Debianie. Dostęp konsoli przez autoryzowane
`virsh console --safe` oraz lokalne ACL storage wymagają odbioru na hoście.

W `evidence/` pakietu są stdout/stderr komend, JUnit, kontrola zachowania
plików, podpisy, pomiary i sprawdzenie archiwum po przeniesieniu. Testy
kontraktów nie zostały policzone jako uruchomione maszyny wirtualne.

## Wejścia i rozmiary

| Receptura | Profil | APK | Archiwa APK | Cały przypięty zestaw wejść |
|---|---|---:|---:|---:|
| `starter` | Codex | 48 | 12,10 MiB | 314,15 MiB |
| `python-tests` | Python | 75 | 31,17 MiB | 212,85 MiB |
| `crypto-math` | Python | 80 | 44,13 MiB | 225,82 MiB |

Wspólne wejście Alpine ma 175,19 MiB. Archiwum Codexa ma 120,35 MiB
i nie należy do locków profilu Python. Są to rozmiary pobranych wejść,
**nie rozmiary gotowych instalacji**. Zachowany cache zawiera także starsze
locki oraz częściowo wspólne zestawy APK; nie należy sumować wierszy jako
rozmiaru pojedynczego obrazu.

Końcowe ponowne zamrożenie po rozszerzeniu formatu receptur trwało odpowiednio
46,887 / 2,483 / 2,468 s i użyło tych samych artefaktów wejściowych. Replay
bez sieci i procesów trwał 0,418 / 0,225 / 0,301 s. Jest to **cache wejść**.
Cache gotowego obrazu pozostaje do rzeczywistego sprawdzenia lokalnego.
Dokładne locki i hashe kodu są w `evidence/INPUT_FREEZE.json` oraz w cache.

`sage-lean4` ma status **EXPERIMENTAL_NOT_BUILT**. Sprawdzono składnię,
44 nazwy wymaganych APK w zachowanych indeksach i piny dodatkowych wejść.
Dużego archiwum Sage nie ma w dostawie i nie wykonano pełnego locka tego
wariantu. Lokalny `template-lock` pobierze je i sprawdzi deklarowany hash.
Szczegóły oraz ograniczenia: `docs/SAGE_LEAN_TEMPLATE_PL.md`.

## Pozostaje lokalnie

| Sprawdzenie | Stan |
|---|---|
| Budowa obrazu przez KVM lub TCG, boot i gotowość gościa | NOT_RUN |
| KVM, systemowy libvirt i konfiguracja storage | BLOCKED w runnerze; odbiór lokalny NOT_RUN |
| Cache gotowego obrazu i dwie działające VM | NOT_RUN |
| Sandbox Codexa wykonany w gościu | NOT_RUN |
| Kompilacja i testy SageMath + Lean 4 | NOT_RUN |
| Faktyczna izolacja hosta i odcięcie aktywnych połączeń | NOT_RUN |
| Egzekwowany transport do OpenAI | NOT_IMPLEMENTED; runner obsługuje tylko offline |
| Zalogowana praca modelu | AWAITING_OPERATOR_LOGIN / NOT_RUN |

Wykryte błędy integracji konsoli, parsowania timeout i kontynuacji demo
naprawiono przed końcowym przebiegiem. Nie przeklasyfikowano ich jako braków
sprzętu. Brak budowy VM wynika z uzgodnionego przekazania tego etapu
operatorowi; nie twierdzimy, że sam brak KVM uniemożliwia wszelką kompilację.

Gałąź robocza zachowuje pochodzenie z `88abbbb4`; archiwalny rdzeń pozostaje
na `archive/qsx-core-pr1-3eb4908`. Bundle zawiera całą odzyskaną dostępną
historię, wcześniejszą pracę Work i nowe commity. Zachowany historyczny obiekt
merge PR pochodzi z GitHuba; nie wykonano nowego merge, push ani zmiany PR #1.
