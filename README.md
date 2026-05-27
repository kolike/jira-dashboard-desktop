# Jira Fast Watcher

Windows tray-приложение для быстрого мониторинга Jira-заявок:

- красные заявки по Коврову с кнопкой `Взять`;
- синие заявки по другим регионам с кнопкой `Взять`;
- отдельный блок моих задач в работе;
- зелёная подсветка только для задач, которые реально находятся в статусе `В работе`;
- Windows toast-уведомления о новых заявках.

## Требования

- Windows 10/11.
- Python 3.12+.
- Jira Personal Access Token.

Проверенная локальная среда:

- Python 3.14.5
- PySide6 6.11.1
- requests 2.34.2
- win11toast 0.36.3
- PyInstaller 6.17.0

## Установка для разработки

```powershell
py -m pip install -r requirements.txt
```

Проверить, что код компилируется:

```powershell
py -m py_compile main.py tray_app.py jira_client.py ui_dashboard.py ui_settings.py ui_completed.py storage.py analytics.py
```

Запустить из исходников:

```powershell
py main.py
```

При первом запуске откройте настройки, укажите Jira Base URL и PAT Token, затем сохраните настройки.

## Сборка exe

Сборка выполняется через PyInstaller по готовому spec-файлу:

```powershell
py -m PyInstaller --clean --noconfirm JiraFastWatcher.spec
```

Готовое приложение появится здесь:

```text
dist\JiraFastWatcher\JiraFastWatcher.exe
```

Запуск:

```powershell
.\dist\JiraFastWatcher\JiraFastWatcher.exe
```

`config.json`, `state.json` и `jira_fast_watcher.log` создаются рядом с exe в папке `dist\JiraFastWatcher`.

## Что важно для сборки

`JiraFastWatcher.spec` уже добавляет в дистрибутив иконки. В one-folder сборке PyInstaller кладет их в служебную папку `dist\JiraFastWatcher\_internal`, откуда приложение читает ресурсы автоматически:

- `app_icon.ico`
- `app_icon.png`
- `icon128.png`

Если добавить новые файлы ресурсов, их нужно также добавить в `datas` внутри `JiraFastWatcher.spec`.

## Рекомендуемая проверка перед релизом

1. Установить зависимости:

```powershell
py -m pip install -r requirements.txt
```

2. Проверить компиляцию:

```powershell
py -m py_compile main.py tray_app.py jira_client.py ui_dashboard.py ui_settings.py ui_completed.py storage.py analytics.py
```

3. Собрать exe:

```powershell
py -m PyInstaller --clean --noconfirm JiraFastWatcher.spec
```

4. Запустить:

```powershell
.\dist\JiraFastWatcher\JiraFastWatcher.exe
```

5. Проверить живой сценарий:

- новая красная заявка приходит в блок `КОВРОВ`;
- новая синяя заявка приходит в блок `РЕГИОНЫ`;
- кнопка `Взять` назначает заявку на текущего пользователя;
- задача появляется в блоке `В РАБОТЕ`;
- активная задача подсвечена зелёным;
- отложенные и прочие мои задачи отображаются без зелёной подсветки.

## GitHub workflow

Обычный цикл изменения:

```powershell
git checkout -b feature/my-change
git add .
git commit -m "Describe change"
git push -u origin feature/my-change
```

После push откройте Pull Request в GitHub из ветки `feature/my-change` в `main`.

Перед merge желательно приложить к PR:

- что изменилось;
- какие проверки выполнены;
- результат сборки exe;
- скриншот главного окна, если менялся UI.
