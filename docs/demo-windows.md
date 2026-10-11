# Демонстрация MIRA Core 0.1 — 26 сентября 2026

> Сохранённые свидетельства обезличены: адреса, имена узлов и фактические локальные пути заменены маркерами. Результаты прежнего запуска сохранены; это не повторная проверка.

Ядро успешно собрано и запущено на Windows 11 Pro x86-64.

## Фактические результаты

| Действие | Результат |
| --- | --- |
| Release-сборка | Успешно, `C:\MIRA\target\release\mira.exe`, 4 046 848 байт |
| `version` | MIRA Core 0.1.0 |
| `status` до запуска | stopped, код выхода 1 |
| `status` после запуска | running, код выхода 0 |
| CLI JSON и `GET /api/v1/status` | Одинаковые `core: 0.1.0`, `status: ok` |
| `GET /api/v1/version` | `core: 0.1.0` |
| CLI и HTTP system | Совпали ОС, архитектура, hostname и общий объём RAM |
| Конфигурация первого запуска | Созданы config/data/logs и TOML |
| Сохранение настроек | node_name=MIRA-Demo и logging.level=info прочитаны после перезапуска |
| Неверный порт 0 | Отклонён, код 1; содержимое TOML не изменилось |
| Второй сервер с той же папкой | Отклонён с сообщением о блокировке, код 1 |
| Файловый журнал | Записаны INFO, DEBUG и ERROR |
| Web UI | Отобразил реальные данные; Online → Offline → Online при остановке и перезапуске |

Данные, полученные самим ядром:

```text
OS: Windows 11 Pro
Architecture: x86_64
CPU: AMD Ryzen 9 7945HX with Radeon Graphics
Logical cores: 32
RAM: 47.22 GiB
Disks: 3
Network interfaces: 6
```

HTTP status:

```json
{"core":"0.1.0","status":"ok"}
```

## Просмотр и команды

Демонстрационный сервер оставлен работающим в фоне на
[http://127.0.0.1:8787](http://127.0.0.1:8787).
Это состояние на момент окончания демонстрации; после перезагрузки компьютера
сервер нужно запустить снова. Автозапуск не настраивался.

Команды PowerShell:

```powershell
cd C:\MIRA
.\target\release\mira.exe --home C:\MIRA\.mira version
.\target\release\mira.exe --home C:\MIRA\.mira status
.\target\release\mira.exe --home C:\MIRA\.mira system
.\target\release\mira.exe --home C:\MIRA\.mira config
Invoke-RestMethod http://127.0.0.1:8787/api/v1/status
```

Для остановки именно фонового демонстрационного процесса с проверкой пути:

```powershell
$miraDemoPid = [int](Get-Content C:\MIRA\.mira\demo.pid)
$miraDemoProcess = Get-Process -Id $miraDemoPid -ErrorAction Stop
if ($miraDemoProcess.Path -eq 'C:\MIRA\target\release\mira.exe') {
    Stop-Process -InputObject $miraDemoProcess
}
```

После остановки можно запустить в своём терминале:

```powershell
.\target\release\mira.exe --home C:\MIRA\.mira serve
```

Остановка такого foreground-запуска — Ctrl+C. В демонстрации использовался
`Stop-Process` для собственного фонового процесса; корректное завершение через
Ctrl+C отдельно не проверялось.

Конфигурация: `C:\MIRA\.mira\config\mira.toml`.
Логи: `C:\MIRA\.mira\logs\`.
Время в журнале — UTC, поэтому файл этой демонстрации имеет дату 2026-09-25.

## Среда сборки

- Rust 1.98.1, target `x86_64-pc-windows-gnu`.
- MinGW-w64 / GCC 16.2.0 MSVCRT, локально в `.tools`.
- `Cargo.lock` создан; успешная сборка выполнена с `--release --locked`.
- Для повторной сборки с подготовленными инструментами: `C:\MIRA\build.ps1`.
- Системный PATH не изменён; `.tools` исключён из Git.
- Для запуска готового `mira.exe` переменные окружения локального toolchain не требуются.

Источники инструментов: [официальный Rust](https://rust-lang.org/tools/install/),
[WinLibs MinGW-w64](https://github.com/brechtsanders/winlibs_mingw/releases/tag/16.2.0posix-14.0.0-msvcrt-r1).
Архив MinGW сверён с опубликованной SHA-512 перед распаковкой.

## Ещё не подтверждено

Linux x86-64, Linux ARM64 и сборка Windows MSVC не запускались.
Полный сценарий приёмки (включая SIGTERM, повреждённый TOML и смену порта)
не выполнялся. Базовая демонстрация Windows прошла; критерий готовности всего
кроссплатформенного релиза 0.1 остаётся открытым.
