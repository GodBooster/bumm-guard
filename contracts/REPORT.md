# BG-NEAR-001 — передача на приймання, 2026-10-30

Додано workspace `contracts/` з near-workspaces 0.24.0, Cargo.lock та
rust-toolchain.toml (1.99.1, wasm32-unknown-unknown). Пакет розміщений у
`sandbox/`, тому наявна перевірка `contracts/*/Cargo.toml` у CI його підхопить.

Тест `sandbox/tests/bg_near_001.rs` компілює оригінальні d1_vuln, d1_safe
і proxy. Перевіряє підробку callback від Mallory, успіх атаки лише на d1_vuln,
конкретну помилку private на d1_safe, paid-стан і відсутність платежу продавцю.
Окремі свіжі розгортання перевіряють легітимний pay → on_paid для обох версій,
включно з точним приростом балансу продавця.

Посилання на вихідні матеріали:
- Спека: ../../research/06_near_detectors_spec_2026-10-29.md, розділ 3, BG-NEAR-001.
- Раннер: ../scripts/linux-test.sh.
- Тулчейн тестів: rust-toolchain.toml.
- Попередній тулчейн: ../../spike-escrow/rust-toolchain.toml (stable).

Важлива розбіжність: реальні фікстури закріплюють near-sdk 5.17.2, а не
зазначену в передачі 5.30.0. Їх та їхній lockfile не змінено.

Перевірка: rustfmt +stable та git diff --check пройшли. Docker-запуск
дійшов до отримання залежностей і створив Cargo.lock. Перший запуск мав
збій встановлення wasm-компонента Rust через паралельне встановлення;
повторний пройшов цей етап. Повний cargo test не завершено: контейнер
зупинено перед передачею користувачу. Компіляція тесту і sandbox-поведінка
залишаються НЕ ПІДТВЕРДЖЕНИМИ; це не звіт про успішний прогін.

Запуск із кореня bumm-guard:

```sh
scripts/linux-test.sh contracts test --workspace --locked
```

Немає комітів, пушу, публікації чи звернень до testnet. Акаунти у коді тесту
створюються виключно в локальному sandbox.
