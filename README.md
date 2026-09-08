# File Viewer

Внутренний read-only сервис предпросмотра файлов по публичной HTTP GET-ссылке. Открывается на отдельной странице и не требует доступа к исходным S3-аккаунтам.

Поддерживаемые сценарии:

- PDF и браузерные изображения передаются потоково с поддержкой `Range`.
- DOC/DOCX, PPT/PPTX, RTF, ODT/ODP преобразуются LibreOffice в PDF.
- XLS/XLSX, ODS, CSV/TSV показываются как интерактивные листы.
- ZIP, 7z, RAR, TAR и GZip показываются как список содержимого.
- ZIP определяется по сигнатуре даже при нестандартном суффиксе.
- OOXML распознаётся по внутренней структуре ZIP и не показывается как архив.
- EML отображает заголовки, безопасное тело и открываемые вложения.
- Вложения архивов и EML можно открывать рекурсивно до заданной глубины.

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

Ответ содержит локальный идентификатор и статус. Пока статус `queued` или `processing`, клиент опрашивает `GET /api/previews/{id}`.

## Жизненный цикл файла

Оригинал всегда остаётся в исходном S3. Реплика скачивает его во временную директорию, создаёт представление и сразу удаляет исходную копию и промежуточные файлы. Результат остаётся только в локальном кэше реплики на `CACHE_TTL_SECONDS` (по умолчанию 24 часа с последнего обращения).

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

При первом запуске автоматически создаются DOCX/XLSX/PPTX, PDF, архивы, EML и файлы с нестандартными суффиксами.

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

Основные переменные перечислены в `.env.example`. Ограничения защищают сервис от слишком больших файлов, zip bomb, чрезмерного количества элементов и глубокой вложенности. HTML из EML очищается, внешние изображения удаляются, тело показывается в sandboxed iframe. Защищённые паролем файлы распознаются, но не открываются.

## Проверки

Unit-тесты запускаются в development-образе:

```powershell
docker build --target test -t file-viewer-test .
docker run --rm file-viewer-test
```

Для полной ручной проверки откройте демо-страницу и последовательно проверьте Office, файл `archive.custom-package`, вложенный ZIP, EML-вложение, защищённый и повреждённый архивы.
