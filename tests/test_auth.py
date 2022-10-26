from .fixtures import app_client
from datasette.utils import baseconv
import pytest
import time


def test_auth_token(app_client):
    """The /-/auth-token endpoint sets the correct cookie"""
    assert app_client.ds._root_token is not None
    path = f"/-/auth-token?token={app_client.ds._root_token}"
    response = app_client.get(
        path,
    )
    assert 302 == response.status
    assert "/" == response.headers["Location"]
    assert {"a": {"id": "root"}} == app_client.ds.unsign(
        response.cookies["ds_actor"], "actor"
    )
    # Check that a second with same token fails
    assert app_client.ds._root_token is None
    assert (
        403
        == app_client.get(
            path,
        ).status
    )


def test_actor_cookie(app_client):
    """A valid actor cookie sets request.scope['actor']"""
    cookie = app_client.actor_cookie({"id": "test"})
    response = app_client.get("/", cookies={"ds_actor": cookie})
    assert {"id": "test"} == app_client.ds._last_request.scope["actor"]


def test_actor_cookie_invalid(app_client):
    cookie = app_client.actor_cookie({"id": "test"})
    # Break the signature
    response = app_client.get("/", cookies={"ds_actor": cookie[:-1] + "."})
    assert None == app_client.ds._last_request.scope["actor"]
    # Break the cookie format
    cookie = app_client.ds.sign({"b": {"id": "test"}}, "actor")
    response = app_client.get("/", cookies={"ds_actor": cookie})
    assert None == app_client.ds._last_request.scope["actor"]


@pytest.mark.parametrize(
    "offset,expected",
    [
        ((24 * 60 * 60), {"id": "test"}),
        (-(24 * 60 * 60), None),
    ],
)
def test_actor_cookie_that_expires(app_client, offset, expected):
    expires_at = int(time.time()) + offset
    cookie = app_client.ds.sign(
        {"a": {"id": "test"}, "e": baseconv.base62.encode(expires_at)}, "actor"
    )
    response = app_client.get("/", cookies={"ds_actor": cookie})
    assert expected == app_client.ds._last_request.scope["actor"]


def test_logout(app_client):
    response = app_client.get(
        "/-/logout", cookies={"ds_actor": app_client.actor_cookie({"id": "test"})}
    )
    assert 200 == response.status
    assert "<p>You are logged in as <strong>test</strong></p>" in response.text
    # Actors without an id get full serialization
    response2 = app_client.get(
        "/-/logout", cookies={"ds_actor": app_client.actor_cookie({"name2": "bob"})}
    )
    assert 200 == response2.status
    assert (
        "<p>You are logged in as <strong>{&#39;name2&#39;: &#39;bob&#39;}</strong></p>"
        in response2.text
    )
    # If logged out you get a redirect to /
    response3 = app_client.get("/-/logout")
    assert 302 == response3.status
    # A POST to that page should log the user out
    response4 = app_client.post(
        "/-/logout",
        csrftoken_from=True,
        cookies={"ds_actor": app_client.actor_cookie({"id": "test"})},
    )
    # The ds_actor cookie should have been unset
    assert response4.cookie_was_deleted("ds_actor")
    # Should also have set a message
    messages = app_client.ds.unsign(response4.cookies["ds_messages"], "messages")
    assert [["You are now logged out", 2]] == messages


@pytest.mark.parametrize("path", ["/", "/fixtures", "/fixtures/facetable"])
def test_logout_button_in_navigation(app_client, path):
    response = app_client.get(
        path, cookies={"ds_actor": app_client.actor_cookie({"id": "test"})}
    )
    anon_response = app_client.get(path)
    for fragment in (
        "<strong>test</strong>",
        '<form action="/-/logout" method="post">',
    ):
        assert fragment in response.text
        assert fragment not in anon_response.text


@pytest.mark.parametrize("path", ["/", "/fixtures", "/fixtures/facetable"])
def test_no_logout_button_in_navigation_if_no_ds_actor_cookie(app_client, path):
    response = app_client.get(path + "?_bot=1")
    assert "<strong>bot</strong>" in response.text
    assert '<form action="/-/logout" method="post">' not in response.text


@pytest.mark.parametrize(
    "post_data,errors,expected_duration",
    (
        ({"expire_type": ""}, [], None),
        ({"expire_type": "x"}, ["Invalid expire duration"], None),
        ({"expire_type": "minutes"}, ["Invalid expire duration"], None),
        (
            {"expire_type": "minutes", "expire_duration": "x"},
            ["Invalid expire duration"],
            None,
        ),
        (
            {"expire_type": "minutes", "expire_duration": "-1"},
            ["Invalid expire duration"],
            None,
        ),
        (
            {"expire_type": "minutes", "expire_duration": "0"},
            ["Invalid expire duration"],
            None,
        ),
        (
            {"expire_type": "minutes", "expire_duration": "10"},
            [],
            600,
        ),
        (
            {"expire_type": "hours", "expire_duration": "10"},
            [],
            10 * 60 * 60,
        ),
        (
            {"expire_type": "days", "expire_duration": "3"},
            [],
            60 * 60 * 24 * 3,
        ),
    ),
)
def test_auth_create_token(app_client, post_data, errors, expected_duration):
    assert app_client.get("/-/create-token").status == 403
    ds_actor = app_client.actor_cookie({"id": "test"})
    response = app_client.get("/-/create-token", cookies={"ds_actor": ds_actor})
    assert response.status == 200
    assert ">Create an API token<" in response.text
    # Now try actually creating one
    response2 = app_client.post(
        "/-/create-token",
        post_data,
        csrftoken_from=True,
        cookies={"ds_actor": ds_actor},
    )
    assert response2.status == 200
    if errors:
        for error in errors:
            assert '<p class="message-error">{}</p>'.format(error) in response2.text
    else:
        # Extract token from page
        token = response2.text.split('value="dstok_')[1].split('"')[0]
        details = app_client.ds.unsign(token, "token")
        assert details.keys() == {"a", "e"}
        assert details["a"] == "test"
        if expected_duration is None:
            assert details["e"] is None
        else:
            about_right = int(time.time()) + expected_duration
            assert about_right - 2 < details["e"] < about_right + 2


def test_auth_create_token_not_allowed_for_tokens(app_client):
    ds_tok = app_client.ds.sign({"a": "test", "token": "dstok"}, "token")
    response = app_client.get(
        "/-/create-token",
        headers={"Authorization": "Bearer dstok_{}".format(ds_tok)},
    )
    assert response.status == 403


@pytest.mark.parametrize(
    "scenario,should_work",
    (
        ("no_token", False),
        ("invalid_token", False),
        ("expired_token", False),
        ("valid_unlimited_token", True),
        ("valid_expiring_token", True),
    ),
)
def test_auth_with_dstok_token(app_client, scenario, should_work):
    token = None
    if scenario == "valid_unlimited_token":
        token = app_client.ds.sign({"a": "test"}, "token")
    elif scenario == "valid_expiring_token":
        token = app_client.ds.sign({"a": "test", "e": int(time.time()) + 1000}, "token")
    elif scenario == "expired_token":
        token = app_client.ds.sign({"a": "test", "e": int(time.time()) - 1000}, "token")
    elif scenario == "invalid_token":
        token = "invalid"
    if token:
        token = "dstok_{}".format(token)
    headers = {}
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    response = app_client.get("/-/actor.json", headers=headers)
    if should_work:
        assert response.json == {"actor": {"id": "test", "token": "dstok"}}
    else:
        assert response.json == {"actor": None}
