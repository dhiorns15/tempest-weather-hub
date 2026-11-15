import json
import os
import re
import struct
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from weatherhub.cache import LatestCache
from weatherhub.db import init_db
from weatherhub.server import make_server

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise AssertionError(f"{path} is not a PNG")
    return struct.unpack(">II", data[16:24])


class TestPwaFilesServed(unittest.TestCase):
    """Content types for the manifest, worker and icons."""

    def setUp(self) -> None:
        self.static_dir = Path(tempfile.mkdtemp())
        (self.static_dir / "manifest.webmanifest").write_text('{"name": "x"}', encoding="utf-8")
        (self.static_dir / "sw.js").write_text("// worker", encoding="utf-8")
        (self.static_dir / "icons").mkdir()
        (self.static_dir / "icons" / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

        self.server = make_server(
            LatestCache(),
            LatestCache(),
            LatestCache(),
            self.db_path,
            self.static_dir,
            port=0,
            cloud_configured=True,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _content_type(self, path: str) -> str:
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            self.assertEqual(response.status, 200)
            return response.headers["Content-Type"]

    def test_manifest_is_served_as_manifest_json(self) -> None:
        self.assertEqual(self._content_type("/manifest.webmanifest"), "application/manifest+json")

    def test_manifest_type_does_not_depend_on_the_host_mime_table(self) -> None:
        with mock.patch(
            "weatherhub.server.mimetypes.guess_type", return_value=("text/plain", None)
        ):
            self.assertEqual(
                self._content_type("/manifest.webmanifest"), "application/manifest+json"
            )

    def test_service_worker_is_served_as_javascript(self) -> None:
        # Browsers refuse to register a worker served with a non-JS type.
        self.assertIn("javascript", self._content_type("/sw.js"))

    def test_icons_are_served_as_png(self) -> None:
        self.assertEqual(self._content_type("/icons/icon.png"), "image/png")


class TestPwaStaticFiles(unittest.TestCase):
    """The shipped manifest, icons and worker are consistent with each other."""

    def setUp(self) -> None:
        self.manifest = json.loads(
            (STATIC_DIR / "manifest.webmanifest").read_text(encoding="utf-8")
        )

    def test_manifest_has_install_fields(self) -> None:
        for key in ("name", "short_name", "start_url", "display", "theme_color", "background_color"):
            self.assertTrue(self.manifest.get(key), key)
        self.assertEqual(self.manifest["display"], "standalone")
        self.assertEqual(self.manifest["start_url"], "/")

    def test_manifest_icons_exist_with_their_declared_sizes(self) -> None:
        declared = set()
        for icon in self.manifest["icons"]:
            path = STATIC_DIR / icon["src"].lstrip("/")
            self.assertTrue(path.is_file(), icon["src"])
            self.assertEqual(icon["type"], "image/png")
            width, height = png_size(path)
            self.assertEqual(icon["sizes"], f"{width}x{height}", icon["src"])
            declared.add((icon["sizes"], icon.get("purpose", "any")))

        for sizes in ("192x192", "512x512"):
            self.assertIn((sizes, "any"), declared)
        self.assertIn(("512x512", "maskable"), declared)

    def test_pages_link_manifest_icons_and_theme_colors(self) -> None:
        for page in ("index.html", "history.html"):
            html = (STATIC_DIR / page).read_text(encoding="utf-8")
            self.assertIn('rel="manifest" href="/manifest.webmanifest"', html, page)
            self.assertIn('rel="apple-touch-icon" href="/icons/apple-touch-icon.png"', html, page)
            self.assertIn('<script src="/js/theme.js"></script>', html, page)
            themes = re.findall(r'name="theme-color" media="\(prefers-color-scheme: (\w+)\)"', html)
            self.assertEqual(sorted(themes), ["dark", "light"], page)

        self.assertEqual(png_size(STATIC_DIR / "icons" / "apple-touch-icon.png"), (180, 180))

    def test_service_worker_precaches_only_files_that_exist(self) -> None:
        worker = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
        match = re.search(r"const PRECACHE = \[(.*?)\];", worker, re.S)
        self.assertIsNotNone(match)
        self.assertIn("OFFLINE_URL", match.group(1))
        offline = re.search(r'const OFFLINE_URL = "(.*?)";', worker).group(1)
        paths = [offline] + re.findall(r'"(/[^"]*)"', match.group(1))
        for path in paths:
            self.assertTrue((STATIC_DIR / path.lstrip("/")).is_file(), path)


if __name__ == "__main__":
    unittest.main()
