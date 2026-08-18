"""
test_pagination.py - Unit tests for smart pagination and viewport optimization.
"""

import math
import unittest
from unittest.mock import MagicMock


def calculate_pagination(total_records, page_size_str, current_page):
    if "10" in page_size_str and "100" not in page_size_str:
        page_size = 10
    elif "25" in page_size_str:
        page_size = 25
    elif "50" in page_size_str:
        page_size = 50
    elif "100" in page_size_str:
        page_size = 100
    elif "200" in page_size_str:
        page_size = 200
    else:
        page_size = max(1, total_records)

    total_pages = max(1, math.ceil(total_records / page_size)) if total_records > 0 else 1
    cur_page = max(1, min(current_page, total_pages))
    start_idx = (cur_page - 1) * page_size
    end_idx = start_idx + page_size
    return cur_page, total_pages, page_size, start_idx, end_idx


class TestPagination(unittest.TestCase):
    def test_slicing_130_records_page_size_50(self):
        records = [f"profile_{i}" for i in range(130)]

        # Page 1
        cur_page, total_pages, page_size, start_idx, end_idx = calculate_pagination(len(records), "50 / trang", 1)
        self.assertEqual(cur_page, 1)
        self.assertEqual(total_pages, 3)
        self.assertEqual(len(records[start_idx:end_idx]), 50)
        self.assertEqual(records[start_idx:end_idx][0], "profile_0")
        self.assertEqual(records[start_idx:end_idx][-1], "profile_49")

        # Page 2
        cur_page, total_pages, page_size, start_idx, end_idx = calculate_pagination(len(records), "50 / trang", 2)
        self.assertEqual(cur_page, 2)
        self.assertEqual(len(records[start_idx:end_idx]), 50)
        self.assertEqual(records[start_idx:end_idx][0], "profile_50")
        self.assertEqual(records[start_idx:end_idx][-1], "profile_99")

        # Page 3
        cur_page, total_pages, page_size, start_idx, end_idx = calculate_pagination(len(records), "50 / trang", 3)
        self.assertEqual(cur_page, 3)
        self.assertEqual(len(records[start_idx:end_idx]), 30)
        self.assertEqual(records[start_idx:end_idx][0], "profile_100")
        self.assertEqual(records[start_idx:end_idx][-1], "profile_129")

    def test_page_size_all_option(self):
        records = [f"profile_{i}" for i in range(250)]
        cur_page, total_pages, page_size, start_idx, end_idx = calculate_pagination(len(records), "Tất cả", 1)
        self.assertEqual(cur_page, 1)
        self.assertEqual(total_pages, 1)
        self.assertEqual(page_size, 250)
        self.assertEqual(len(records[start_idx:end_idx]), 250)

    def test_clamping_out_of_bounds(self):
        records = [f"profile_{i}" for i in range(40)]
        # Request page 99 when only 1 page exists
        cur_page, total_pages, _, start_idx, end_idx = calculate_pagination(len(records), "50 / trang", 99)
        self.assertEqual(cur_page, 1)
        self.assertEqual(total_pages, 1)

        # Request page -5
        cur_page, total_pages, _, _, _ = calculate_pagination(len(records), "50 / trang", -5)
        self.assertEqual(cur_page, 1)

    def test_empty_records(self):
        cur_page, total_pages, page_size, start_idx, end_idx = calculate_pagination(0, "50 / trang", 1)
        self.assertEqual(cur_page, 1)
        self.assertEqual(total_pages, 1)
        self.assertEqual(start_idx, 0)
        self.assertEqual(end_idx, 50)

    def test_slicing_25_records_page_size_10(self):
        records = [f"profile_{i}" for i in range(25)]

        # Page 1 (10 items)
        cur_page, total_pages, page_size, start_idx, end_idx = calculate_pagination(len(records), "10 / trang", 1)
        self.assertEqual(cur_page, 1)
        self.assertEqual(total_pages, 3)
        self.assertEqual(page_size, 10)
        self.assertEqual(len(records[start_idx:end_idx]), 10)
        self.assertEqual(records[start_idx:end_idx][0], "profile_0")
        self.assertEqual(records[start_idx:end_idx][-1], "profile_9")

        # Page 2 (10 items)
        cur_page, total_pages, page_size, start_idx, end_idx = calculate_pagination(len(records), "10 / trang", 2)
        self.assertEqual(cur_page, 2)
        self.assertEqual(len(records[start_idx:end_idx]), 10)
        self.assertEqual(records[start_idx:end_idx][0], "profile_10")
        self.assertEqual(records[start_idx:end_idx][-1], "profile_19")

        # Page 3 (5 items)
        cur_page, total_pages, page_size, start_idx, end_idx = calculate_pagination(len(records), "10 / trang", 3)
        self.assertEqual(cur_page, 3)
        self.assertEqual(len(records[start_idx:end_idx]), 5)
        self.assertEqual(records[start_idx:end_idx][0], "profile_20")
        self.assertEqual(records[start_idx:end_idx][-1], "profile_24")

    def test_page_sizes_parsing(self):
        self.assertEqual(calculate_pagination(100, "10 / trang", 1)[2], 10)
        self.assertEqual(calculate_pagination(100, "25 / trang", 1)[2], 25)
        self.assertEqual(calculate_pagination(100, "50 / trang", 1)[2], 50)
        self.assertEqual(calculate_pagination(100, "100 / trang", 1)[2], 100)
        self.assertEqual(calculate_pagination(100, "200 / trang", 1)[2], 200)


if __name__ == "__main__":
    unittest.main()
