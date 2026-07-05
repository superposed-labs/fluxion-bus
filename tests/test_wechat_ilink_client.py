from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from fluxion.channels.wechat.ilink_client import ILinkClient


def test_upload_file_to_cdn_includes_http_error_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "image.png"
    file_path.write_bytes(b"image")
    error = HTTPError(
        "https://cdn.example/upload",
        500,
        "Internal Server Error",
        hdrs=None,
        fp=BytesIO(b'{"error":"invalid upload"}'),
    )

    def raise_http_error(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)

    with pytest.raises(RuntimeError, match='HTTP 500: \\{"error":"invalid upload"\\}'):
        ILinkClient().upload_file_to_cdn(
            upload_url="https://cdn.example/upload",
            file_path=file_path,
            aes_key="00112233445566778899aabbccddeeff",
            key_is_hex=True,
        )
