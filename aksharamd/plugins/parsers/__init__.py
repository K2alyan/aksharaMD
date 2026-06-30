from . import pdf, markdown, html, text
from . import docx, pptx, spreadsheet, epub, data, notebook, email, archive, image, eml
from . import odf, archive_tar, rtf, legacy_office, audio
from . import rst  # must come after text so it overrides the TextParser fallback for .rst

__all__ = [
    "pdf", "markdown", "html", "text",
    "docx", "pptx", "spreadsheet", "epub", "data", "notebook", "email", "archive", "image", "eml",
    "odf", "archive_tar", "rtf", "legacy_office", "audio",
    "rst",
]
