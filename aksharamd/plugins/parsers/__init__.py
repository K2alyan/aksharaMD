from . import (
    archive,
    archive_tar,
    audio,
    data,
    docx,
    email,
    eml,
    epub,
    html,
    image,
    legacy_office,
    markdown,
    notebook,
    odf,
    pdf,
    pptx,
    rst,  # must come after text so it overrides the TextParser fallback for .rst
    rtf,
    spreadsheet,
    text,
)

__all__ = [
    "pdf", "markdown", "html", "text",
    "docx", "pptx", "spreadsheet", "epub", "data", "notebook", "email", "archive", "image", "eml",
    "odf", "archive_tar", "rtf", "legacy_office", "audio",
    "rst",
]
