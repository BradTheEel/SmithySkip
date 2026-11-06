# SmithySkip

SmithySkip ускоряет обычные реплики при открытии сервисов в Monster Hunter: World. Версия **0.6.7** рассчитана на Steam build `15539686` и включает 23 профиля диалогов на русском и английском. Удерживай **Shift** или **RB** на геймпаде XInput, чтобы временно отключить автозакрытие и ускорение.

[Список сервисов](COVERAGE_RU.md) · [История изменений](CHANGELOG.md) · [Лицензия MIT](LICENSE)

## Сборка и установка

Из корня репозитория запусти сборку с Zig 0.13.0.

```powershell
.\build.ps1 -ZigPath 'C:\путь\к\zig.exe'
```

Готовый плагин появится в `build/SmithySkip.dll`. Закрой игру, сохрани резервную копию прежней DLL и помести новую в `nativePC\plugins`. Для загрузки плагина используй установленный Stracker’s Loader.

Сборка использует готовый `src/profiles.h`. Лицензия MinHook и HDE находится в `src/vendor/LICENSE.txt`.

## Стендовые тесты

Для регенерации профилей и проверок диалогов нужны Python x64 3.9 или новее и отдельный исследовательский каталог с `inspect_dialog.py` и `extracted/npc`. Укажи его путь в `SMITHY_RESEARCH`. Нативные тесты запускай через отдельный owned-test launcher. В примере `$launcher` и `$python` содержат пути к исполняемым файлам.

```powershell
& $launcher $python -B .\generate_profiles.py
.\build.ps1 -ZigPath 'C:\путь\к\zig.exe'
& $launcher $python -B .\test_plugin.py
& $launcher $python -B .\test_controller_bypass.py
& $launcher $python -B .\test_profiles.py
& $launcher $python -B .\test_no_logging_selfcheck.py
& $launcher $python -B .\test_no_logging.py --dll .\build\SmithySkip.dll --scratch (Join-Path $PWD ('no-log-' + [guid]::NewGuid().ToString('N')))
```

Проверки журнала можно выполнить после сборки. Им достаточно тестовых скриптов, Python, launcher и готовой DLL.
