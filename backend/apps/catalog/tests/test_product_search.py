"""完整关键词搜索合约；可见性和商品摘要沿用公开目录规则。"""

from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory


pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog():
    assert connection.vendor == "mysql"
    assert connection.settings_dict["NAME"].startswith("test_")
    mice = Category.objects.create(name="鼠标", slug="mice", is_active=True)
    other = Category.objects.create(name="配件", slug="accessories", is_active=True)
    hidden = Category.objects.create(name="隐藏分类", slug="hidden", is_active=False)
    products = [
        Product.objects.create(category=category, slug=slug, name=name, description=description, is_active=active)
        for category, slug, name, description, active in (
            (mice, "wireless-m1", "无线鼠标 M1", "蓝牙双模，适合办公", True),
            (mice, "description-hit", "桌面配件", "备用无线鼠标", True),
            (other, "other-category", "无线鼠标适配器", "配件", True),
            (mice, "restocking", "无线鼠标待补货", "无库存", True),
            (mice, "inactive-product", "无线鼠标下架", "不可见", False),
            (hidden, "inactive-category", "无线鼠标隐藏", "不可见", True),
        )
    ]
    for code, price, active, stock in (("M1-A", "129.00", True, 0), ("M1-B", "149.00", True, 5), ("M1-H", "1.00", False, 99)):
        sku = SKU.objects.create(product=products[0], code=code, price=Decimal(price), is_active=active)
        Inventory.objects.create(sku=sku, available_stock=stock)
    return APIClient(enforce_csrf_checks=True), mice, products


def search(catalog, **params):
    return catalog[0].get(reverse("catalog:product-list"), params)


def slugs(response):
    assert response.status_code == 200
    return [product["slug"] for product in response.json()["results"]]


def test_keyword_matches_name_or_description_with_original_public_summary(catalog):
    with CaptureQueriesContext(connection) as queries:
        response = search(catalog, q="无线鼠标")
    assert response.json()["count"] == 4
    assert set(slugs(response)) == {"wireless-m1", "description-hit", "other-category", "restocking"}
    assert len(queries) == 2
    by_slug = {product["slug"]: product for product in response.json()["results"]}
    assert by_slug["wireless-m1"] == {
        "slug": "wireless-m1", "name": "无线鼠标 M1", "category": {"slug": "mice", "name": "鼠标"},
        "lowest_price": "129.00", "in_stock": True,
    }
    assert by_slug["description-hit"]["lowest_price"] is None
    assert by_slug["description-hit"]["in_stock"] is False
    assert slugs(search(catalog, q="蓝牙")) == ["wireless-m1"]
    assert slugs(search(catalog, q="m1")) == ["wireless-m1"]


def test_keyword_and_category_intersect_without_hidden_content(catalog):
    assert set(slugs(search(catalog, q="无线鼠标", category="mice"))) == {
        "wireless-m1", "description-hit", "restocking",
    }
    assert slugs(search(catalog, q="无线鼠标", category="hidden")) == []
    assert slugs(search(catalog, q="无线鼠标", category="unknown")) == []


def test_empty_or_whitespace_keyword_keeps_existing_browse_and_missing_phrase_is_empty(catalog):
    original = search(catalog).json()
    assert search(catalog, q="").json() == original
    assert search(catalog, q="  \t ").json() == original
    assert search(catalog, q="  无线鼠标  ").json()["results"] == search(catalog, q="无线鼠标").json()["results"]
    with CaptureQueriesContext(connection) as queries:
        response = search(catalog, q="不可能匹配的测试词")
    assert response.json() == {"count": 0, "next": None, "previous": None, "results": []}
    assert len(queries) == 1
    # Whole-keyword inclusion deliberately does not reorder or split these words.
    assert slugs(search(catalog, q="鼠标 无线")) == []


@pytest.mark.parametrize("keyword", ["%", "_", "\\"])
def test_like_wildcards_are_literal_text(catalog, keyword):
    Product.objects.create(category=catalog[1], name=f"字面符号{keyword}商品", slug="literal-symbol", is_active=True)
    assert slugs(search(catalog, q=keyword)) == ["literal-symbol"]


def test_query_length_and_null_character_fail_before_product_sql(catalog):
    for keyword in ("x" * 81, "bad\x00keyword"):
        with CaptureQueriesContext(connection) as queries:
            response = search(catalog, q=keyword)
        assert response.status_code == 400
        assert "q" in response.json()
        assert len(queries) == 0
    assert search(catalog, q="x" * 80).status_code == 200
    assert slugs(search(catalog, q="x' OR 1=1 --")) == []


def test_same_name_results_have_stable_id_pages_and_keep_search_in_links(catalog):
    expected = []
    for index in range(25):
        product = Product.objects.create(
            category=catalog[1], name="同名搜索商品", slug=f"same-name-{index:02d}", is_active=True,
        )
        expected.append(product.slug)
    first = search(catalog, q="同名搜索商品", category="mice", page_size=100)
    assert first.json()["count"] == 25
    assert slugs(first) == expected[:20]
    query = parse_qs(urlsplit(first.json()["next"]).query)
    assert query["q"] == ["同名搜索商品"] and query["category"] == ["mice"] and query["page"] == ["2"]
    second = search(catalog, q="同名搜索商品", category="mice", page=2)
    assert slugs(second) == expected[20:]
    assert second.json()["next"] is None
