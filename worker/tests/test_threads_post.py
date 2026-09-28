import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tasks.threads_tasks import scrape_threads_post


def page_with_media(post):
    inner = {"__bbox": {"result": {"data": {"media": post}}}}
    outer = {"__bbox": {"require": [[None, None, None, [None, inner]]]}}
    data = {"require": [[None, None, None, [outer]]]}
    page = MagicMock()
    page.css.return_value = [SimpleNamespace(text=json.dumps(data))]
    page.html_content = ""
    return page


class ThreadsPostTest(unittest.TestCase):
    def test_current_media_layout(self):
        post = {"code": "DdLa4azCZCg", "like_count": 1, "caption": {"text": "ok"}, "text_post_app_info": {"direct_reply_count": 3}}
        with patch("tasks.threads_tasks.FetcherSession") as fetcher, patch("config.settings.threads_session_id", ""):
            fetcher.return_value.__enter__.return_value.get.return_value = page_with_media(post)
            result = scrape_threads_post("itsmetxsya", post["code"])
        self.assertEqual((result["likes"], result["comments"], result["caption"]), (1, 3, "ok"))

    def test_missing_post_fails_job(self):
        with patch("tasks.threads_tasks.FetcherSession") as fetcher:
            fetcher.return_value.__enter__.return_value.get.return_value = page_with_media({})
            with self.assertRaisesRegex(ValueError, "Could not parse Threads post data"):
                scrape_threads_post("itsmetxsya", "DdLa4azCZCg")


if __name__ == "__main__":
    unittest.main()
