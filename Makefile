# Lumen — raccourcis de développement.
.PHONY: help install dev api web build serve clean

PY := backend/.venv/bin/python
PIP := backend/.venv/bin/pip

help:
	@echo "make install   crée le venv, installe le backend et le frontend"
	@echo "make api       lance l'API sur :3041 (rechargement à chaud)"
	@echo "make web       lance l'interface sur :3040 (proxy /api vers :3041)"
	@echo "make build     construit le frontend dans frontend/dist"
	@echo "make serve     production : un seul processus sert l'API et l'interface"
	@echo "make clean     supprime le venv, node_modules et le build"

install:
	python3 -m venv backend/.venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt
	cd frontend && npm install
	@test -f backend/.env || cp .env.example backend/.env
	@echo "Prêt. « make api » dans un terminal, « make web » dans un autre."

api:
	cd backend && .venv/bin/uvicorn app.main:app --port 3041 --reload

web:
	cd frontend && npm run dev

build:
	cd frontend && npm run build

serve: build
	cd backend && .venv/bin/uvicorn app.main:app --port 3041

clean:
	rm -rf backend/.venv frontend/node_modules frontend/dist backend/__pycache__
