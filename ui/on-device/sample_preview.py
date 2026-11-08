"""Validate an explicitly selected, local JPEG for display-only UI previews."""
from pathlib import Path
import stat

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QImageReader


def image_url(value=None):
    """Return an empty URL for normal preview; never contact a camera service."""
    if value is None:
        return QUrl()
    try:
        path=Path(value).resolve(strict=True)
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 32*1024*1024:
            raise ValueError('Sample preview requires a local JPEG smaller than 32 MiB')
        reader=QImageReader(str(path))
        size=reader.size()
        if (bytes(reader.format()).lower() not in (b'jpeg',b'jpg') or not reader.canRead()
                or not size.isValid() or max(size.width(),size.height()) > 16384
                or size.width()*size.height() > 100_000_000):
            raise ValueError('Sample preview requires a readable local JPEG')
        return QUrl.fromLocalFile(str(path))
    except (OSError,TypeError) as error:
        raise ValueError('Sample preview requires a readable local JPEG') from error
