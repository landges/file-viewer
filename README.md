# File Viewer

Внутренний read-only сервис предпросмотра файлов по публичной HTTP GET-ссылке или локальной загрузке. Открывается на отдельной странице и не требует доступа к исходным S3-аккаунтам.

Поддерживаемые сценарии:

- Локальный файл можно перетащить на стартовую страницу или выбрать через системный диалог.
- PDF и браузерные изображения передаются потоково с поддержкой `Range`.
- WAV и MP3 воспроизводятся встроенным браузерным плеером с поддержкой перемотки.
- XML и JSON показываются с безопасным форматированием, подсветкой и переключением на исходный текст; HTML/HTM — после очистки в sandboxed iframe.
- DOC/DOCX, PPT/PPTX, RTF, ODT/ODP преобразуются LibreOffice в PDF.
- XLS/XLSX, ODS, CSV/TSV показываются как интерактивные листы.
- ZIP, 7z, RAR, TAR и GZip показываются как список содержимого.
- ZIP определяется по сигнатуре даже при нестандартном суффиксе.
- OOXML распознаётся по внутренней структуре ZIP и не показывается как архив.
- EML и Outlook MSG отображают заголовки, безопасное тело и открываемые вложения.
- Вложения архивов, EML и Outlook MSG можно открывать рекурсивно до заданной глубины.

## Интеграция

Приложение формирует обычную ссылку:

```js
const viewer = new URL("http://viewer.internal/view");
viewer.searchParams.set("url", publicFileUrl);
viewer.searchParams.set("name", originalFilename);

// Для обычного <a> используйте target="_blank" и rel="noopener".
window.open(viewer, "_blank", "noopener");
```

API также доступно напрямую:

```http
POST /api/previews
Content-Type: application/json

{"url":"http://storage.internal/files/document.docx","filename":"Договор.docx"}
```

Локальная загрузка использует multipart endpoint `POST /api/uploads` с полем `file`.

Ответ содержит локальный идентификатор и статус. Пока статус `queued` или `processing`, клиент опрашивает `GET /api/previews/{id}`.

## Жизненный цикл файла

Для URL оригинал остаётся в исходном S3: реплика скачивает его во временную директорию, создаёт представление и сразу удаляет исходную копию и промежуточные файлы. Перетащенный пользователем файл хранится вместе с представлением в локальном кэше, чтобы работали скачивание и открытие вложений. Все локальные данные удаляются по `CACHE_TTL_SECONDS` (по умолчанию 24 часа с последнего обращения).

Закрытие вкладки не используется как сигнал удаления: браузер не гарантирует отправку такого события. Очистка выполняется фоновым заданием по TTL. После падения реплики или попадания на другую ноду файл преобразуется повторно.

## Локальный запуск

Нужен Docker Desktop с Docker Compose.

### Production-like режим

```powershell
docker compose up --build
```

- демо приложения: <http://localhost:8082>
- viewer через Traefik со sticky cookie: <http://localhost:8080>
- публичный тестовый источник: <http://localhost:8081/files/>
- Traefik dashboard: <http://localhost:8088>

При первом запуске автоматически создаются DOCX/RTF/XLSX/PPTX, PDF, WAV, MP3, XML, JSON, HTML/HTM, архивы, EML, Outlook MSG и файлы с нестандартными суффиксами.

Автоматический сквозной тест всей поднятой среды:

```powershell
docker compose --profile test run --rm smoke
```

### Режим разработки с hot reload

```powershell
docker compose -f docker-compose.dev.yml up --build
```

- frontend: <http://localhost:5173>
- backend и Swagger: <http://localhost:8080/docs>
- демо: <http://localhost:8082/?dev=1>

Код `backend/app` подключён в контейнер bind mount. Frontend также подключён как volume, поэтому оба процесса автоматически перечитывают изменения. Для отладки backend из PyCharm можно переопределить команду контейнера или подключиться к процессу Python внутри development-образа.

## Docker Swarm

1. Соберите образ и отправьте его во внутренний registry.
2. Создайте внешнюю overlay-сеть `proxy`, если её ещё нет.
3. Передайте переменные `FILE_VIEWER_IMAGE`, `VIEWER_HOST` и `SOURCE_ALLOWED_HOSTS`.
4. Разверните `deploy/docker-stack.yml`.

```powershell
docker build -t registry.internal/file-viewer:0.1.0 .
docker push registry.internal/file-viewer:0.1.0
$env:FILE_VIEWER_IMAGE='registry.internal/file-viewer:0.1.0'
$env:VIEWER_HOST='viewer.internal'
$env:SOURCE_ALLOWED_HOSTS='*.s3.internal,storage.internal'
docker stack deploy -c deploy/docker-stack.yml file-viewer
```

В stack-файле включена sticky cookie Traefik и ограничение `max_replicas_per_node: 1`. Том `viewer-cache` использует локальный драйвер: на каждой Swarm-ноде существует собственный независимый кэш.

## Настройка и безопасность

`SOURCE_ALLOWED_HOSTS` — обязательный список внутренних хостов или масок. Не используйте `*` в production: viewer имеет сетевой доступ к внутренней инфраструктуре, поэтому произвольный URL создаст SSRF-канал. На каждом redirect схема, хост и порт проверяются повторно.

`SOURCE_TLS_INSECURE_HOSTS` — список точных имён хостов, для которых отключается проверка HTTPS-сертификата. Исключение не распространяется на поддомены, HTTP-запросы или хосты, на которые ведёт redirect. Предпочтительный вариант — установить внутренний CA; insecure-список используйте только для явно доверенных legacy-источников.

Основные переменные перечислены в `.env.example`. Ограничения защищают сервис от слишком больших файлов, zip bomb, чрезмерного количества элементов и глубокой вложенности. HTML-файлы и HTML из EML/MSG очищаются, внешние изображения удаляются, содержимое показывается в sandboxed iframe. XML и JSON форматируются лексически, выводятся как текст и не исполняются; исходная запись больших JSON-чисел, порядок и дублирующиеся ключи не меняются. Поддерживаются BOM, объявления кодировки XML/HTML и распространённые однобайтовые арабские кодировки, включая Windows-1256, ISO-8859-6 и CP720. Для короткого текста без BOM или объявления точное различение совместимых однобайтовых кодировок невозможно, поэтому применяется эвристика. Защищённые паролем файлы распознаются, но не открываются.

## Проверки

Unit-тесты запускаются в development-образе:

```powershell
docker build --target test -t file-viewer-test .
docker run --rm file-viewer-test
```

Для полной ручной проверки откройте демо-страницу и последовательно проверьте Office, файл `archive.custom-package`, вложенный ZIP, EML/MSG-вложения, защищённый и повреждённый архивы.
