# SocialFetch ⚡

Prywatna stronka do pobierania filmów i zdjęć z **Instagrama, TikToka i X**, bez znaku wodnego, bez reklam i bez popupów.

- 🎬 filmy w wybranej jakości (TikTok od razu bez watermarka)
- 🖼 zdjęcia i karuzele (+ wszystko naraz w `.zip`)
- 🎵 dźwięk jako MP3 (też z TikTokowych pokazów zdjęć)
- 🔒 dostęp na hasło: tylko ty
- 📱 działa na telefonie, da się zainstalować jako apkę. Na Androidzie pojawia się w menu „Udostępnij”.

## Jak to działa

| Platforma | Silnik |
|---|---|
| TikTok wideo | [yt-dlp](https://github.com/yt-dlp/yt-dlp): API TikToka podaje wersję bez znaku wodnego |
| TikTok zdjęcia | [gallery-dl](https://github.com/mikf/gallery-dl) |
| Instagram | yt-dlp (zdjęcia z karuzel wyciągamy z jego metadanych) |
| X / Twitter | publiczne API [FxTwitter](https://github.com/FixTweet/FxTwitter), yt-dlp jako zapas |

Backend: FastAPI + ffmpeg. Frontend: czysty HTML/CSS/JS w `app/static`.
Pliki nie są nigdzie trzymane: ściągają się do katalogu tymczasowego, lecą do przeglądarki i od razu są kasowane.

## Deploy za darmo na Render

1. Wrzuć repo na GitHuba (już jest 😎).
2. Wejdź na [render.com](https://render.com) i zaloguj się GitHubem (karta niepotrzebna).
3. **New + → Blueprint** → wybierz to repo. Render sam znajdzie `render.yaml`.
4. Wpisz swoje **`APP_PASSWORD`** (hasło do strony) → **Apply**.
5. Po kilku minutach dostajesz adres w stylu `https://socialfetch-xxxx.onrender.com`.

**Uwaga:** darmowa instancja zasypia po ~15 minutach bez ruchu. Pierwsze wejście po przerwie trwa około 30–60 s, potem już śmiga. Przy każdym starcie yt-dlp i gallery-dl aktualizują się same (`AUTO_UPDATE=1`), więc jak platforma coś zmieni, zwykle wystarczy chwilę poczekać.

## Instagram blokuje? Dodaj cookies

Serwery w chmurze mają IP, których Instagram nie lubi. Po kilku zapytaniach może zacząć żądać logowania. Fix:

1. Załóż **zapasowe** konto na IG (nie używaj głównego, bo może dostać bana).
2. Zaloguj się na nie w przeglądarce i wyeksportuj cookies w formacie Netscape, np. rozszerzeniem „Get cookies.txt LOCALLY”.
3. W Renderze: serwis → **Environment → Secret Files** → dodaj plik `cookies.txt` z tą zawartością.
4. Dodaj zmienną **`COOKIES_FILE=/etc/secrets/cookies.txt`** i zrób redeploy.

## Uruchomienie lokalnie

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # potrzebny też ffmpeg w systemie
APP_PASSWORD=haslo .venv/bin/uvicorn app.main:app --reload
# albo Dockerem:
docker build -t socialfetch . && docker run -p 8000:8000 -e APP_PASSWORD=haslo socialfetch
```

Otwórz http://localhost:8000.

## Zmienne środowiskowe

| Zmienna | Opis |
|---|---|
| `APP_PASSWORD` | hasło do strony (bez niego strona jest otwarta dla każdego!) |
| `SECRET_KEY` | klucz do podpisywania ciasteczka logowania (Render generuje go sam) |
| `COOKIES_FILE` | ścieżka do `cookies.txt` (opcjonalnie, głównie pod Instagram) |
| `AUTO_UPDATE` | `1` = aktualizuj yt-dlp/gallery-dl przy starcie (domyślnie) |
| `MAX_PARALLEL` | ile pobierań naraz (domyślnie 2, bo darmowy plan ma mało RAM-u) |

## Ograniczenia

- Działa tylko dla publicznych postów. Prywatne konta, stories i relacje wymagają cookies.
- Tweety mieszające zdjęcia i filmy działają. Na IG karuzele mieszane też.
- To narzędzie do użytku prywatnego. Szanuj twórców i nie wrzucaj ich treści jako swoich.
