up:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f --tail=100

etl:
	docker compose exec api python -m app.etl.run --tenant alpha-store --csv /data/large_dataset.csv
