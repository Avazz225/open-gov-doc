import httpx
from dms_cli.main import app


def test_user_list(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(
        route_json(
            {
                "GET /api/auth-service/users": [
                    {"id": "u1", "username": "alice", "email": "a@x.test", "enabled": True}
                ]
            }
        )
    )

    result = runner.invoke(app, ["user", "list"])

    assert result.exit_code == 0, result.output
    assert "alice" in result.output


def test_user_create_prompts_for_password_when_not_given(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "u2", "username": "bob"})

    mock_transport_factory(handler)

    result = runner.invoke(
        app,
        [
            "user",
            "create",
            "--username",
            "bob",
            "--email",
            "b@x.test",
            "--first-name",
            "Bob",
            "--last-name",
            "Bauer",
        ],
        input="s3cret\n",
    )

    assert result.exit_code == 0, result.output
    assert captured["body"]["password"] == "s3cret"


def test_user_delete(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(route_json({"DELETE /api/auth-service/users/u1": (204, None)}))

    result = runner.invoke(app, ["user", "delete", "u1"])

    assert result.exit_code == 0, result.output


def test_role_list(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(
        route_json(
            {
                "GET /api/permission-service/roles": [
                    {"id": 1, "name": "reviewer", "permissions": []}
                ]
            }
        )
    )

    result = runner.invoke(app, ["role", "list"])

    assert result.exit_code == 0, result.output
    assert "reviewer" in result.output


def test_role_assignment_list_with_filters(cli_home, runner, logged_in, mock_transport_factory):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    mock_transport_factory(handler)

    result = runner.invoke(
        app, ["role", "assignment", "list", "--principal", "u1", "--resource", "folder:1"]
    )

    assert result.exit_code == 0, result.output
    assert captured["query"] == {"principal_id": "u1", "resource_id": "folder:1"}


def test_role_assignment_create(cli_home, runner, logged_in, mock_transport_factory):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "id": 5,
                "principal_type": "user",
                "principal_id": "u1",
                "role_id": 1,
                "resource_id": "root",
            },
        )

    mock_transport_factory(handler)

    result = runner.invoke(
        app,
        [
            "role",
            "assignment",
            "create",
            "--principal-type",
            "user",
            "--principal",
            "u1",
            "--role",
            "1",
            "--resource",
            "root",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["body"] == {
        "principal_type": "user",
        "principal_id": "u1",
        "role_id": 1,
        "resource_id": "root",
    }


def test_role_assignment_delete(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(
        route_json({"DELETE /api/permission-service/role-assignments/5": (204, None)})
    )

    result = runner.invoke(app, ["role", "assignment", "delete", "5"])

    assert result.exit_code == 0, result.output
    assert "entfernt" in result.output
