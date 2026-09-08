from __future__ import annotations

import io
import shutil
import subprocess
import sys
import zipfile
from email.message import EmailMessage
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches


def main() -> None:
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures/generated")
    output.mkdir(parents=True, exist_ok=True)

    create_image(output / "image.png")
    create_docx(output / "document.docx")
    create_xlsx(output / "table.xlsx")
    create_pptx(output / "presentation.pptx")
    (output / "readme.txt").write_text("Тестовый текстовый файл.\nВторая строка.\n", encoding="utf-8")
    (output / "data.csv").write_text("Название;Количество;Цена\nКофе;4;120.50\nЧай;7;85\n", encoding="utf-8")

    create_archives(output)
    create_email(output / "message.eml", output / "document.docx")
    (output / "broken.zip").write_bytes(b"PK\x03\x04this is deliberately damaged")
    convert_legacy_and_pdf(output)
    (output / ".fixtures-version").write_text("1\n", encoding="ascii")
    print(f"Generated fixtures in {output}")


def create_image(path: Path) -> None:
    image = Image.new("RGB", (1200, 700), "#15211b")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((90, 90, 1110, 610), radius=36, fill="#23372d", outline="#65e6a1", width=5)
    draw.text((150, 300), "FILE VIEWER / IMAGE", fill="#dfffea", font=None)
    image.save(path)


def create_docx(path: Path) -> None:
    document = Document()
    document.add_heading("Тестовый документ", 0)
    document.add_paragraph("Этот DOCX создан автоматически для локальной проверки предпросмотра.")
    document.add_heading("Проверяем", level=1)
    for value in ("кириллицу и шрифты", "списки", "таблицы", "конвертацию в PDF"):
        document.add_paragraph(value, style="List Bullet")
    table = document.add_table(rows=3, cols=2)
    table.cell(0, 0).text = "Поле"
    table.cell(0, 1).text = "Значение"
    table.cell(1, 0).text = "Статус"
    table.cell(1, 1).text = "Готово"
    table.cell(2, 0).text = "Версия"
    table.cell(2, 1).text = "0.1"
    document.save(path)


def create_xlsx(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Товары"
    sheet.append(["Название", "Количество", "Цена", "Сумма"])
    for row, values in enumerate((("Кофе", 4, 120.5), ("Чай", 7, 85), ("Какао", 3, 150)), start=2):
        sheet.append([*values, f"=B{row}*C{row}"])
    summary = workbook.create_sheet("Описание")
    summary.append(["Книга", "Локальная тестовая таблица"])
    summary.append(["Листов", 2])
    workbook.save(path)


def create_pptx(path: Path) -> None:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = "File Viewer"
    slide.placeholders[1].text = "Тестовая презентация"
    second = presentation.slides.add_slide(presentation.slide_layouts[5])
    second.shapes.title.text = "Вторая страница"
    box = second.shapes.add_textbox(Inches(1), Inches(2), Inches(8), Inches(1))
    box.text_frame.text = "PPTX преобразуется в PDF через LibreOffice."
    presentation.save(path)


def create_archives(output: Path) -> None:
    nested_buffer = io.BytesIO()
    with zipfile.ZipFile(nested_buffer, "w", zipfile.ZIP_DEFLATED) as nested:
        nested.writestr("inside/note.txt", "Вложенный архив открыт успешно.\n")
    archive_path = output / "archive.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("docs/readme.txt", "Файл внутри архива.\n")
        archive.write(output / "table.xlsx", "docs/table.xlsx")
        archive.writestr("nested.zip", nested_buffer.getvalue())
        archive.writestr("empty/", b"")
    shutil.copy2(archive_path, output / "archive.custom-package")
    try:
        subprocess.run(
            ["7z", "a", "-t7z", "-pviewer", "-mhe=on", str(output / "protected.7z"), str(output / "readme.txt")],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass


def create_email(path: Path, attachment: Path) -> None:
    message = EmailMessage()
    message["From"] = "sender@example.internal"
    message["To"] = "receiver@example.internal"
    message["Subject"] = "Проверка EML и вложения"
    message.set_content("Текстовая версия тестового письма.")
    message.add_alternative(
        """
        <html><body>
          <h2>Тестовое письмо</h2>
          <p>Внешнее изображение и скрипт должны быть заблокированы.</p>
          <img src="http://tracking.invalid/pixel.gif">
          <script>alert('not allowed')</script>
        </body></html>
        """,
        subtype="html",
    )
    message.add_attachment(
        attachment.read_bytes(),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="Вложенный документ.docx",
    )
    path.write_bytes(message.as_bytes())


def convert_legacy_and_pdf(output: Path) -> None:
    conversions = [
        (output / "document.docx", "pdf"),
        (output / "document.docx", "doc"),
        (output / "table.xlsx", "xls"),
        (output / "presentation.pptx", "ppt"),
    ]
    for source, target in conversions:
        try:
            subprocess.run(
                ["soffice", "--headless", "--convert-to", target, "--outdir", str(output), str(source)],
                check=True,
                timeout=90,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue


if __name__ == "__main__":
    main()

