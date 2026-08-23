import app as web_app


def test_custom_background_upload_limit_is_32_mib():
    assert web_app.app.config["MAX_CONTENT_LENGTH"] == 32 * 1024 * 1024


def test_unknown_route_remains_404():
    response = web_app.app.test_client().get("/does-not-exist")
    assert response.status_code == 404
