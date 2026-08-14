from fastapi import APIRouter, Response

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.api_route("/api/hello", methods=["GET", "HEAD"])
def anthropic_hello() -> Response:
    return Response(status_code=200)
