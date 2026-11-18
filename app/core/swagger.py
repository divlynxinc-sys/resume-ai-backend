import os

from fastapi.openapi.utils import get_openapi

tags_metadata = [
    {"name": "Auth", "description": "Signup, login, refresh tokens, logout."},
    {"name": "Profile", "description": "User self profile operations."},
    {"name": "Admin", "description": "Admin-only endpoints."},
    {"name": "Dashboard", "description": "Summary, recent activity, suggestions."},
    {"name": "Resumes", "description": "Create, list, update, duplicate, delete resumes and section content."},
    {"name": "Templates", "description": "List and query templates."},
]


def setup_swagger(app):
    # Configure docs paths and UI behavior. Disabled in production so the API
    # schema isn't publicly readable; still available in local/dev.
    is_production = os.getenv("APP_ENV", "local") == "production"
    app.docs_url = None if is_production else "/docs"
    app.redoc_url = None if is_production else "/redoc"
    app.openapi_url = None if is_production else "/openapi.json"
    app.swagger_ui_parameters = {
        "persistAuthorization": True,
        "displayRequestDuration": True,
    }

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=getattr(app, "version", "1.0.0"),
            description=getattr(app, "description", ""),
            routes=app.routes,
            tags=tags_metadata,
        )
        # JWT bearer auth
        schema.setdefault("components", {}).setdefault("securitySchemes", {}).update(
            {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "Paste only the access token; 'Bearer' prefix is added automatically by docs UI.",
                }
            }
        )
        schema["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi
    return app

