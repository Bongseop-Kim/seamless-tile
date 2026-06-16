# Duego API

되고시스템(Duegosystem) 웹 API 서버. FastAPI 기반 골격 프로젝트.

## 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`.env.example`를 복사해 `.env`를 만들고 값을 채웁니다.

## 실행

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- API: `http://localhost:8000/api/v1/health`
- 문서(Swagger): `http://localhost:8000/docs`

## 구조

```
app/
├── main.py            # FastAPI 앱 생성 (create_app)
├── core/config.py     # 환경설정 (pydantic-settings)
└── api/routes/        # 라우터 모음
    └── health.py      # /health
tests/                 # pytest 테스트
```

## 테스트

```bash
.venv/bin/python -m pytest
```
