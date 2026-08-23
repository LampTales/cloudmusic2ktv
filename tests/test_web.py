import app as web_app


def test_unknown_route_remains_404():
    response = web_app.app.test_client().get("/does-not-exist")
    assert response.status_code == 404
