# Bramka LG

Desktopowy kontroler bramki ACMC Gateway, umożliwiający monitorowanie i sterowanie jednostkami LG przez Modbus TCP.

## Funkcje

- Skanowanie do 250 jednostek i prezentowanie wykrytych urządzeń w tabeli.
- Podgląd stanu bramki, typu jednostki, zasilania, trybu pracy, nawiewu, temperatur oraz flag i błędów.
- Automatyczne odświeżanie danych wybranej jednostki co 2 sekundy.
- Sterowanie pojedynczą jednostką lub wysyłanie nastaw do wszystkich jednostek.
- Ustawianie zasilania, temperatury zadanej (18–30 °C), trybu pracy i nawiewu.
- Obsługa flag Plazma (PF), Blokada panelu (PL) i Auto Swing (AS).
- Szybkie wyłączenie wszystkich jednostek.
- Eksport wykrytych jednostek i ich danych do pliku CSV.

## Połączenie

Aplikacja komunikuje się z bramką przez Modbus TCP. Wprowadź adres IP bramki i port (domyślnie `502`), wybierz jednostkę, a następnie uruchom skanowanie lub włącz automatyczne odświeżanie.

Do działania wymagane są bramka ACMC Gateway obsługująca Modbus TCP oraz dostęp sieciowy do bramki. Sterowanie wieloma jednostkami może zmienić ich ustawienia jednocześnie — przed wysłaniem nastaw sprawdź wybrane parametry.

## Technologie

- Python
- CustomTkinter
- pymodbus
