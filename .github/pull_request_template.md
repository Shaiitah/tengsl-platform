## Что изменено

- 

## Что проверено

- [ ] `python scripts/release_check.py`
- [ ] Python syntax
- [ ] JavaScript syntax
- [ ] Docker Compose config
- [ ] Backend tests
- [ ] Edge-agent tests

## Регрессии

- [ ] Существующие функции не удалены
- [ ] `PLATFORM_FEATURES.yml` обновлён только при намеренном изменении контракта
- [ ] Изменение БД сопровождается миграцией/планом миграции

## База данных

- [ ] Backup перед изменением схемы выполнен
- [ ] Restore/Merge не затронуты, если изменение к ним не относится
