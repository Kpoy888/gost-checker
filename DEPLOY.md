# Как выложить сайт бесплатно (ноябрь 2026)

Сайт — тот же `gost-checker`: страница на русском, загрузка чертежа (DXF, PDF, PNG/JPG), отчёт и исправленный DXF. Платные облачные API не нужны.

**Hugging Face Spaces с Docker SDK не подходит как бесплатный путь:** и Docker, и обычный Gradio (CPU Basic) требуют платный план. Бесплатными на HF остаются статические Spaces и до двух ZeroGPU (это GPU/PyTorch, не наш CPU-сервис с Tesseract). Обёртка приложения в Gradio бесплатного хостинга не даёт.

Облачные платформы, которые мы проверяли (Hugging Face, Render, Koyeb, Fly.io, Railway, Google Cloud Run), на ноябрь 2026 либо платные, либо при регистрации просят банковскую карту — даже если сам тариф называется Free.

**Актуальный путь без аккаунта и без карты:** запустить сайт на Linux-компьютере и открыть его в интернет через Cloudflare Quick Tunnel (`trycloudflare.com`). Аккаунт Cloudflare не нужен. Скрипт: [`scripts/run_public.sh`](scripts/run_public.sh).

Раздел про Render ниже оставлен на случай, если карта у вас уже есть и вы всё же хотите облачный хостинг.

Официальные страницы Render (если пойдёте этим путём):

- тарифы: <https://render.com/pricing>
- ограничения Free: <https://render.com/docs/free>
- первый деплой («This tutorial uses free Render resources. No payment is required.»): <https://render.com/docs/your-first-deploy>
- Blueprint (`render.yaml`): <https://render.com/docs/blueprint-spec>
- минуты сборки: <https://render.com/docs/build-pipeline>


## Свой компьютер + Cloudflare Quick Tunnel (без аккаунта и без карты)

Так сайт работает прямо у вас (или на учебной машине): локально слушает порт `7860`, а наружу Cloudflare выдаёт временный адрес `https://….trycloudflare.com`. Карту и регистрацию не спрашивают.

### Что будет запущено

1. Веб-сервер в цикле `scripts/serve_forever.sh` — если процесс упадёт, он сам поднимется снова. Лог: `logs/web.log`.
2. Туннель в цикле `scripts/tunnel_forever.sh`: команда  
   `cloudflared tunnel --url http://localhost:7860 --no-autoupdate`.  
   Лог: `logs/tunnel.log`. Свежий публичный адрес пишется в `PUBLIC_URL.txt`.

**Адрес Quick Tunnel меняется при каждом перезапуске `cloudflared`.** Это не постоянное имя. После `stop` / перезагрузки компьютера / падения туннеля откройте `PUBLIC_URL.txt` или `logs/tunnel.log` и возьмите новый URL.

Официальные материалы Cloudflare: [Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/) и бинарник [cloudflared Releases](https://github.com/cloudflare/cloudflared/releases) (`cloudflared-linux-amd64` для обычного 64-битного Linux).

### Шаги на своём Linux-ПК

1. Скопируйте проект (из GitHub `Kpoy888/gost-checker` или из `gost-site.zip`) и установите зависимости:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip tesseract-ocr tesseract-ocr-rus curl
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# по желанию, чтобы открывать DWG:
# bash scripts/install_dwg.sh
```

2. Из корня проекта:

```bash
bash scripts/run_public.sh
```

Скрипт сам скачает официальный `cloudflared` (в `~/.local/bin/cloudflared`, на этой учебной машине — в `/workspace/bin/cloudflared`), поднимет сайт и туннель и напечатает публичный адрес.

3. Откройте напечатанный `https://….trycloudflare.com` в браузере. Локально страница также доступна как `http://127.0.0.1:7860/`.

Полезные команды:

```bash
bash scripts/run_public.sh status   # PID и текущий публичный адрес
bash scripts/run_public.sh stop     # остановить сайт и туннель
# после stop снова:
bash scripts/run_public.sh          # будет НОВЫЙ адрес trycloudflare.com
```

Перезапуск «вручную» (если не пользуетесь `run_public.sh`):

```bash
# остановить
kill "$(cat logs/web.supervisor.pid)" "$(cat logs/tunnel.supervisor.pid)"
# запустить
nohup bash scripts/serve_forever.sh >> logs/web.supervisor.out 2>&1 &
nohup bash scripts/tunnel_forever.sh >> logs/tunnel.supervisor.out 2>&1 &
cat PUBLIC_URL.txt
```

### Ограничения Quick Tunnel

- URL случайный и **непостоянный** (меняется после рестарта туннеля).
- Для учебной публикации и демонстрации этого достаточно; постоянное своё доменное имя потребовало бы аккаунта Cloudflare и настроенного Named Tunnel.
- Компьютер должен быть включён и иметь доступ в интернет, пока кто-то открывает сайт.
- Загруженные чертежи живут на этой машине около часа (`JOB_TTL_SEC = 3600`) и пропадают при перезапуске процесса.
- Если браузер показывает ошибку Cloudflare **530** (Tunnel error / 1033), адрес `trycloudflare.com` уже выдан, но процесс не смог подключиться к сети Cloudflare. Так бывает, когда провайдер или учебный/корпоративный прокси закрывает исходящие UDP/TCP на порту **7844** (протокол туннеля). Сайт при этом всё равно работает локально: `http://127.0.0.1:7860/`. Можно повторить запуск с `--protocol http2`; если не поможет — выполните `bash scripts/run_public.sh` на домашнем Linux с обычным интернетом (без перехвата DNS и без блокировки 7844). Документация Cloudflare: <https://developers.cloudflare.com/tunnel/troubleshooting/>.

### Запасные туннели (без карты)

Если Cloudflare Quick Tunnel выдаёт 530, `scripts/run_public.sh` сам переключается на `scripts/tunnel_alt_forever.sh`. Можно выбрать сервис явно:

```bash
TUNNEL_ALT=pinggy bash scripts/run_public.sh
# или только запасной цикл:
TUNNEL_ALT=localtunnel nohup bash scripts/tunnel_alt_forever.sh >> logs/tunnel_alt.supervisor.out 2>&1 &
```

Порядок в режиме `TUNNEL_ALT=auto`: localtunnel → pinggy → serveo → localhost.run → tunnelmole → bore.

| Сервис | Как поднимается | Ограничения |
| --- | --- | --- |
| localtunnel (`npx localtunnel --port 7860`) | URL `https://….loca.lt`. Клиент открывает сырой TCP на случайный порт сервера. | Бывает промежуточная страница; для API добавьте заголовок `bypass-tunnel-reminder: true`. |
| Pinggy | `ssh -p 443 -R0:localhost:7860 a.pinggy.io` | Бесплатная сессия около **60 минут**, потом нужен новый запуск и будет новый URL. Иногда показывают предупреждение. |
| serveo.net | `ssh -R 80:localhost:7860 serveo.net` (порт 22) | Адрес вида `https://….serveo.net`. Сервис иногда недоступен. |
| localhost.run | `ssh -p 443 -R 80:localhost:7860 nokey@localhost.run` | Адрес `https://….lhr.life` / `….localhost.run`. Аккаунт не нужен. |
| tunnelmole | `npx tunnelmole 7860` | Ходит по WebSocket на `service.tunnelmole.com:8083` (это не 443). |
| bore.pub | `bore local 7860 --to bore.pub` | Обычно `http://bore.pub:<порт>`, без HTTPS. |
| zrok | нужен бесплатный аккаунт и `zrok enable` | Пропускаем, пока не исчерпаны варианты без регистрации. |
| ngrok | нужен бесплатный аккаунт и authtoken | Запасной платный-по-регистрации вариант, скриптами не запускается. |

`cloudflared` не умеет строить dataplane Quick Tunnel через обычный HTTPS на порту 443: используются QUIC/HTTP2 на **7844**. Флаги `--protocol http2` и `--edge-ip-version auto` / `--region us` эту блокировку не обходят.

На сети, которая подменяет DNS и пропускает наружу только обычный HTTPS (без SSH, без произвольных TCP-портов и без WebSocket), ни один из перечисленных туннелей не поднимет dataplane — сайт останется на `http://127.0.0.1:7860/`. Тогда запустите `bash scripts/run_public.sh` на домашнем Linux с обычным интернетом.

## Render (если готовы указать карту)

## Лимиты бесплатного тарифа Render

| Параметр | Значение |
| --- | --- |
| План сервиса | `free`: 0,1 CPU и **512 МБ ОЗУ** |
| Карта | Не требуется. Без карты при превышении лимитов сервис **приостанавливают**, а не выставляют счёт. |
| Сон | После **15 минут** без запросов сервис засыпает. Первый визит будит его примерно за **1 минуту** (Render показывает страницу ожидания). Пока сервис спит, часы не расходуются. |
| Часы работы | **750** бесплатных часов инстанса на workspace в календарный месяц. |
| Сборка | У Hobby workspace обычно **500** минут пайплайна в месяц (сборочная машина 2 CPU / 8 ГБ, таймаут сборки 120 мин). LibreDWG по умолчанию **не компилируется**, чтобы уложиться в этот лимит. |
| Диск | Эфемерный: загруженные чертежи пропадают при перезапуске и при засыпании. В приложении результаты ещё и удаляются примерно через час (`JOB_TTL_SEC = 3600`). |
| Сеть | Исходящий трафик считается в месячную квоту. Без карты при исчерпании полосы сервисы приостанавливают до следующего месяца. |

Приложение настроено под 512 МБ: переменные `GOST_LIGHT_MEMORY=1` и `GOST_MAX_IMAGE_SIDE=1600`, схема линий рисуется через Pillow, превью DXF — с пониженным DPI. Очень большой скан всё равно может не поместиться; обычный учебный лист А3 проходит.

DWG на бесплатном деплое по умолчанию **не читается** (нет заранее собранного LibreDWG). Экспортируйте вид в DXF из nanoCAD, Renga или Revit. Как включить DWG — в конце файла.

## Что залить на GitHub

Репозиторий: <https://github.com/Kpoy888/gost-checker> (публичный). Не кладите туда `.venv/`, `vendor/` и каталог `out/`.

Обязательные файлы:

- `Dockerfile`, `render.yaml`, `requirements.txt`, `README.md`
- `gost_check.py`, каталоги `gost_checker/` и `templates/`
- `scripts/install_dwg.sh` (понадобится, только если потом включите сборку LibreDWG)
- `scripts/run_public.sh`, `scripts/serve_forever.sh`, `scripts/tunnel_forever.sh`, `scripts/tunnel_alt_forever.sh`
- полезно: `DEPLOY.md`, `samples/titleblock_example.json`, учебные `samples/bad.dxf` и остальные примеры, `tests/`

## Способ А. Blueprint из `render.yaml` (предпочтительный)

После того как репозиторий `Kpoy888/gost-checker` уже существует и в нём есть `render.yaml`:

1. Откройте [render.com](https://render.com) и нажмите **Get Started** (или сразу [dashboard.render.com/register](https://dashboard.render.com/register)).
2. Выберите **Sign up with GitHub** и разрешите доступ. Карту вводить не нужно.
3. Если GitHub ещё не привязан: **Account Settings** → раздел **Account Security** / **Git Deployment Credentials** → **Add credential** → авторизуйте GitHub. Дайте Render доступ к репозиторию `gost-checker` (или ко всем своим репозиториям).
4. В правом верхнем углу дашборда нажмите **New** (или **+ New**).
5. В списке выберите **Blueprint**.
6. Найдите и выберите репозиторий **gost-checker** (`Kpoy888/gost-checker`), нажмите **Connect**, если кнопки ещё нет.
7. Render прочитает `render.yaml` и покажет сервис `gost-checker`: runtime Docker, план **free**, регион **frankfurt**, проверка живости `/health`.
8. Нажмите **Apply** (применить / создать ресурсы) внизу.
9. Откроется страница деплоя. Следите за логами **Build** / **Deploy**. Первая сборка обычно 5–15 минут: ставятся системные пакеты (Tesseract с русским языком) и зависимости Python. Компиляции LibreDWG нет.
10. Когда статус станет **Live**, на странице сервиса будет адрес вида `https://gost-checker-xxxx.onrender.com`. Откройте его.
11. Перетащите учебный `samples/bad.dxf`. Если сервис только что проснулся, первая загрузка страницы может занять около минуты.

Последующие `git push` в ветку, указанную при создании (обычно `main`), сами запускают новую сборку.

## Способ Б. Вручную: New → Web Service

Если Blueprint по какой-то причине недоступен:

1. Зарегистрируйтесь и привяжите GitHub, как в способе А.
2. В правом верхнем углу: **New** → **Web Service**.
3. В списке репозиториев выберите **gost-checker** и нажмите **Connect**.
4. Заполните форму:
   - **Name** — например `gost-checker`;
   - **Language** / runtime — **Docker** (не Python: нужен Tesseract в образе);
   - **Branch** — `main`;
   - **Region** — **Frankfurt** (ближе к Европе);
   - **Instance type** / **Compute** — **Free** (0,1 CPU, 512 MB).
5. Раскройте **Advanced** (дополнительно):
   - **Health Check Path** — `/health`.
6. Переменные окружения (если их ещё нет; в Blueprint они уже заданы):

   | Ключ | Значение |
   | --- | --- |
   | `GOST_LIGHT_MEMORY` | `1` |
   | `GOST_MAX_IMAGE_SIDE` | `1600` |
   | `MPLBACKEND` | `Agg` |
   | `BUILD_LIBREDWG` | `0` |
   | `PYTHONUNBUFFERED` | `1` |

   `PORT` Render выставляет сам — его переопределять не нужно. Контейнер слушает `${PORT}`.
7. Нажмите **Deploy Web Service** (кнопка внизу формы).
8. Дождитесь статуса **Live** и откройте выданный `*.onrender.com`.

## Проверка после запуска

- Откройте сайт и перетащите `samples/bad.dxf` (есть исправления и отчёт, превью «до/после»).
- Затем PDF и PNG: для них будет только отчёт, без «исправленного файла».
- Служебная проверка: `https://<ваш-адрес>/health` должен ответить, что сервис жив.
- Первый запрос после сна занимает около минуты — это нормально для бесплатного плана.

## Как включить чтение DWG (необязательно)

По умолчанию `BUILD_LIBREDWG=0`: сборка быстрая, DWG нужно экспортировать в DXF.

Чтобы собрать GNU LibreDWG в образе:

1. На странице сервиса откройте **Environment**.
2. Измените `BUILD_LIBREDWG` на `1` (или добавьте переменную с этим значением). Render передаёт переменные и как Docker build-arg.
3. **Manual Deploy** → **Deploy latest commit** (лучше с очисткой кэша, если такая галочка есть: **Clear build cache**).
4. Первая такая сборка занимает примерно 10–20 минут и расходует минуты пайплайна (лимит около 500 в месяц).

Запись обратно в DWG у LibreDWG возможна только в формате r2000; основным исправленным файлом остаётся DXF.

## Если что-то пошло не так

- Сборка упала по времени или по памяти — убедитесь, что `BUILD_LIBREDWG=0`.
- Сайт открылся, а проверка большого скана оборвалась — файл слишком тяжёлый для 512 МБ; уменьшите изображение или загрузите DXF/PDF учебного размера.
- Статус Failed в логах стартера: контейнер должен слушать `$PORT`. В `Dockerfile` это уже учтено (`uvicorn ... --port ${PORT:-7860}`).
- Исчерпаны 750 часов или минуты сборки — сервис встанет до следующего календарного месяца (без карты счёт не выставят).

## Другие варианты, которые проверялись (ноябрь 2026)

- **Hugging Face Spaces.** Docker и Gradio CPU Basic — платный план. Static Space не запустит Python/Tesseract. ZeroGPU — не тот тип железа.
- **Koyeb.** Для регистрации нужна карта (предоплата/антифрод). Не подходит как вариант «без карты».
- **Fly.io.** Нет постоянного бесплатного тарифа; пробный период roughly $1 / 7 дней без карты, дальше карта и оплата (самый маленький всегда включённый инстанс — около $2/мес).
- **Railway.** Пробный период ($5 кредита / 30 дней) без карты, затем Free plan с кредитом около $1/мес или Hobby $5/мес с картой. Хуже подходит как основной бесплатный хост, чем Render.
- **Google Cloud Run.** Есть always-free квота, но billing-аккаунт и платёжный метод обязательны.
- **PythonAnywhere** (бесплатный аккаунт). Нет Docker, 100 CPU-секунд в день, мало диска, веб-приложение с месячным сроком; Tesseract и LibreDWG штатно не поставить. Для этого проекта не годится.
- **Локально, совсем без облака:** в каталоге проекта `python -m gost_checker.web` (или `uvicorn gost_checker.web:app --host 127.0.0.1 --port 7860`) после установки зависимостей и Tesseract. Это запасной путь, если облачный хост недоступен.
