import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import { useKeycloak } from '@react-keycloak/web';
import ReportPage from './ReportPage';

jest.mock('@react-keycloak/web', () => ({ useKeycloak: jest.fn() }));

const period = { date_from: '2026-11-08', date_to: '2026-11-10' };
const ready = { status: 'ready', timezone: 'UTC', period, row_count: 1,
  items: [{ report_date: '2026-11-10', prosthesis_id: '581949c9-9087-543e-aeb8-5b18a9e7e70e',
    model: 'DEMO-Hand-A', movement_count: 3, avg_response_time_ms: 100 }] };
let container, root, keycloak, downloads;

beforeEach(() => {
  jest.useFakeTimers('modern');
  jest.setSystemTime(new Date('2026-11-14T12:00:00Z'));
  global.IS_REACT_ACT_ENVIRONMENT = true;
  process.env.REACT_APP_API_URL = 'http://localhost:8000';
  keycloak = { authenticated: true, token: 'demo-access',
    updateToken: jest.fn().mockResolvedValue(false), login: jest.fn().mockResolvedValue(undefined),
    idTokenParsed: { preferred_username: 'user1' } };
  useKeycloak.mockReturnValue({ keycloak, initialized: true });
  global.fetch = jest.fn();
  downloads = [];
  URL.createObjectURL = jest.fn().mockReturnValue('blob:ui-test');
  URL.revokeObjectURL = jest.fn();
  jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
    downloads.push(this.download);
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<ReportPage />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  jest.runOnlyPendingTimers();
  jest.useRealTimers();
  jest.restoreAllMocks();
});

function selectPeriod(from = period.date_from, to = period.date_to) {
  act(() => {
    Simulate.change(container.querySelector('#date-from'), { target: { value: from } });
    Simulate.change(container.querySelector('#date-to'), { target: { value: to } });
  });
}

async function submit() {
  await act(async () => { Simulate.submit(container.querySelector('form')); });
}

test('форма передаёт выбранные даты в API и инициирует скачивание JSON', async () => {
  fetch.mockResolvedValue({ status: 200, json: async () => ready });
  selectPeriod();
  await submit();
  expect(fetch.mock.calls[0][0]).toContain('date_from=2026-11-08&date_to=2026-11-10');
  expect(downloads).toEqual(['bionicpro-report-2026-11-08_2026-11-10.json']);
  expect(container.querySelector('[role="status"]').textContent).toContain('Строк: 1');
});

test('пустой готовый отчёт скачивается и объясняется отдельно', async () => {
  fetch.mockResolvedValue({ status: 200, json: async () => ({ ...ready, status: 'empty', row_count: 0, items: [] }) });
  selectPeriod();
  await submit();
  expect(downloads).toHaveLength(1);
  expect(container.querySelector('[role="status"]').textContent).toContain('Период обработан');
  expect(container.textContent).toContain('не означает отсутствие активности');
});

test('при 409 показывает недостающие даты без скачивания, а при смене дат убирает старое сообщение', async () => {
  fetch.mockResolvedValue({ status: 409, json: async () => ({ missing_dates: ['2026-11-07'] }) });
  selectPeriod('2026-11-07');
  await submit();
  expect(downloads).toEqual([]);
  expect(container.querySelector('[role="alert"]').textContent).toContain('2026-11-07');
  selectPeriod();
  expect(container.querySelector('[role="alert"]')).toBeNull();
});

test('блокирует повторный запрос, изменение периода и выход до завершения запроса', async () => {
  let finish;
  fetch.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  selectPeriod();
  await submit();
  expect(container.querySelector('fieldset').disabled).toBe(true);
  expect(container.querySelector('button[type="submit"]').disabled).toBe(true);
  await submit();
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { finish({ status: 200, json: async () => ready }); });
  expect(container.querySelector('button[type="submit"]').disabled).toBe(false);
  expect(downloads).toHaveLength(1);
});

test('ошибка обновления входа не скачивает файл и предлагает повторную аутентификацию', async () => {
  keycloak.updateToken.mockRejectedValue(new Error('refresh failed'));
  selectPeriod();
  await submit();
  expect(downloads).toEqual([]);
  expect(fetch).not.toHaveBeenCalled();
  const login = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Войти снова');
  await act(async () => Simulate.click(login));
  expect(keycloak.login).toHaveBeenCalledWith({ prompt: 'login', redirectUri: window.location.origin + '/' });
});

test('без входа форма отчёта недоступна', () => {
  keycloak.authenticated = false;
  act(() => root.render(<ReportPage />));
  expect(container.querySelector('form')).toBeNull();
  expect(container.textContent).toContain('Войдите, чтобы скачать отчёт');
  expect(fetch).not.toHaveBeenCalled();
});
