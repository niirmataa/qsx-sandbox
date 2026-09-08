# Osobny szablon Alpine: SageMath + Lean 4

**Status: `EXPERIMENTAL_NOT_BUILT`.** Receptura `templates/sage-lean4.toml`
jest kandydatem do lokalnej kompilacji. Sprawdzono nazwy APK, źródła, hashe
archiwów Lean/CaDiCaL/leantar i kontrakt receptury. Nie wykonano kompilacji Sage
ani Lean na Alpine 3.24, testów gotowego obrazu ani odbioru KVM. To nie jest
gotowy działający obraz i nie ma statusu `HOST_VALIDATED`.

Szablon zachowuje Alpine 3.24.1 x86_64 i dodaje SageMath 10.8 oraz Lean 4.33.1
z narzędziami Lake, CaDiCaL i leantar. Osobny `agent-profiles/codex.toml`
wybiera klienta agenta. Mathlib nie jest częścią tej receptury: wymaga własnego
przypiętego kompletu źródeł i zależności. Małe szablony pozostają niezależne.

## Dlaczego kompilacja ze źródeł

W lokalnych indeksach oficjalnych repozytoriów Alpine v3.24 `main` i
`community` dla x86_64 nie znaleziono pakietów `sage`, `sagemath`, `lean` ani
`lean4`. Indeksy obejmowały odpowiednio 5962 i 22671 nazw. To ustalenie dotyczy
tego zamrożonego zestawu, a nie wszystkich architektur czy przyszłych wydań.

Sage publikuje pełne archiwum źródłowe z katalogiem `upstream/` zawierającym
standardowe zależności. Oficjalna dokumentacja przewiduje użycie go przy
budowie offline. Receptura używa utrzymywanej klasycznej ścieżki
`./configure` i `make build`; obecna dokumentacja ogólna rekomenduje również
nowszą ścieżkę Meson. Alpine ma w źródłach Sage własną listę minimalnych
pakietów. Istnienie tej listy nie dowodzi zgodności całego wydania z Alpine
3.24, musl i GCC 15. [Źródła Sage](https://github.com/sagemath/sage/releases/tag/10.8),
[budowa offline](https://doc.sagemath.org/html/en/installation/source-distro.html),
[minimalne pakiety Alpine](https://github.com/sagemath/sage/blob/10.8/build/pkgs/_prereq/distros/alpine.txt).

Lean jest budowany z dołączonych źródeł bootstrapu `stage0`. CaDiCaL powstaje
z osobnego archiwum źródłowego, a leantar pochodzi z jawnego wydania dla
`x86_64-unknown-linux-musl`. Gotowe ścieżki `CADICAL` i `LEANTAR`, wyłączone
mimalloc i zdalny cache Lake eliminują automatyczne pobieranie tych zależności
przez konfigurację CMake. [Instrukcja Lean](https://github.com/leanprover/lean4/blob/v4.33.1/doc/make/index.md),
[konfiguracja zależności](https://github.com/leanprover/lean4/blob/v4.33.1/CMakeLists.txt),
[CaDiCaL](https://github.com/arminbiere/cadical/blob/rel-2.1.2/BUILD.md).

## Przypięte wejścia

Pełne adresy URL i SHA-256 znajdują się bezpośrednio w czterech sekcjach
`[[artifacts]]` receptury. Są pobierane wyłącznie przez `template-lock`.

| Wejście | Wersja / commit źródeł | Rozmiar pobrania |
|---|---|---:|
| Sage, pełne źródła | 10.8 | 1 380 364 760 B |
| Lean, źródła z bootstrapem | 4.33.1 / `819816b2e0a3bf405af45ae5c7af2491d8f5bee6` | 86 700 313 B |
| CaDiCaL, źródła | 2.1.2 / `3ff42f04384489916f017acd6d5e7cbfa7257be7` | 727 767 B |
| leantar, binarium musl | 0.1.20 | 1 163 691 B |

Hash Sage pochodzi z oficjalnych metadanych wydania GitHub; dużego archiwum
nie pobierano podczas przygotowania tej receptury. Pozostałe trzy archiwa
pobrano do sprawdzenia i obliczono ich SHA-256; wynik leantar odpowiada także
metadanym wydania. Przypięcie SHA-256 tych dodatkowych archiwów oznacza
kontrolę integralności, nie deklarację niezależnie zweryfikowanego podpisu
wydawcy. Podpisy bazy Alpine, jej pakietów i komponentów Codex mają własną
istniejącą ścieżkę weryfikacji.

Wersje APK i ich pełna pula zależności są rozwiązywane oraz zamrażane podczas
`template-lock`. Receptura nie zawiera `apk add sage`, instalatora `latest`,
`elan` pobierającego toolchain ani polecenia sieciowego w `prepare`.

## Lokalny przebieg

Z katalogu projektu, po przygotowaniu narzędzia i wymagań hosta:

```sh
qsx-alpine template-check templates/sage-lean4.toml
qsx-alpine template-lock templates/sage-lean4.toml --cache /srv/qsx-cache
```

`template-lock` wypisze rzeczywistą ścieżkę locka. Dopiero wtedy użyj jej
w komendzie budowy; wskazany plik wyjściowy nie może jeszcze istnieć:

```sh
qsx-alpine image-build /SCIEZKA/WYPISANA/PRZEZ/template-lock.json \
  --cache /srv/qsx-cache \
  --output /srv/qsx-images/sage-lean4-first.qcow2 \
  --policy policies/research.example.toml \
  --timeout 43200
```

Przykładowa polityka jawnie dopuszcza 16 GiB RAM, 8 vCPU i dysk wirtualny
64 GiB. Domyślna polityka 4 GiB/4 vCPU odmówi tej recepturze. Wartości trzeba
dostosować do rzeczywistych zasobów hosta; nie są pomiarem zapotrzebowania.
Skrypt kompilacji używa czterech zadań równolegle. Limit 43200 sekund jest
przykładowym limitem operatora, a nie obietnicą czasu budowy.

Kontroler przenosi archiwa na nośnik tylko do odczytu pod
`/mnt/qsx-build/artifacts/`. Kopiowanie, rozpakowanie i wykonanie źródeł
odbywa się wyłącznie w VM. Kompilacja pracuje jako nie-root `agent`; etap
instalacji nadaje katalogom `/opt/sage-10.8` i `/opt/lean-4.33.1` właściciela
root. Sage pozostaje instalacją w swoim katalogu źródłowym. Builder nie
podłącza sieci gościa. Brakujący plik lub niezgodność kompilatora ma
zatrzymać budowę, nie uruchomić pobieranie zastępcze.

Po udanej kompilacji istniejące bramki buildera uruchomią jako `agent`
kontrolę wersji, arytmetykę Sage, `lake --version` i prosty dowód Lean.
Dopiero rzeczywisty wynik tych kontroli może dać gotowemu obrazowi status
budowy. Nie zastępuje on pełnych testów obu projektów ani odbioru izolacji
hosta. Z gotowego obrazu tworzy się odrębne prywatne dyski QSX zwykłym
`instance-create`, wskazując tę samą jawną politykę zasobów.

Lokalna praca z Astrą powinna zachować lock, log budowy i pierwszą konkretną
przyczynę błędu, poprawić recepturę lub przypięte źródła, a następnie utworzyć
nowy lock i nowy obraz. Nie deklarujemy rozmiaru gotowej instalacji,
powtarzalności bitowej ani sukcesu kompilacji przed tym odbiorem. Dane
projektów, poświadczenia i logowanie operatora pozostają poza bazą.

## Dopuszczony wariant zapasowy: Docker w VM

Operator dopuścił cięższy wariant, jeśli natywna kompilacja na Alpine okaże
się przeszkodą na tym etapie. Systemem VM zarządzanej przez QSX pozostaje
Alpine; SageMath i Lean mogą działać w kontenerach wewnątrz niej. Kontener
może dostarczyć środowisko użytkowe właściwe dla narzędzi. Silnik Docker
działa wtedy w gościu, bez dostępu do socketa Docker hosta.

To osobna receptura, z jawnie dobranymi zasobami, obrazami przypiętymi
digestami i zachowanymi archiwami OCI/Docker do ładowania offline. QSX nadal
ogranicza całą VM, a profil agenta pozostaje niezależny. Nie wolno przedstawiać
takiego obrazu jako natywnej kompilacji Sage/Lean na musl.

Osobna receptura `templates/sage-lean4-container.toml` i jawny helper freeze
są teraz zaimplementowane. Aktualny stan odbioru, ograniczenia wrapperów,
przypięte źródła i komendy znajdują się w
[SAGE_CONTAINER_WORKSHOP_PL.md](SAGE_CONTAINER_WORKSHOP_PL.md).
Niniejsza receptura natywna pozostaje `EXPERIMENTAL_NOT_BUILT`; wdrożenie
wariantu kontenerowego nie jest dowodem jej kompilacji.
