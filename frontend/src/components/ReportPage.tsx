import React, { useRef, useState } from 'react';
import { useKeycloak } from '@react-keycloak/web';
import { downloadReportFile, ReportError, requestReport, yesterdayUtc } from '../reports';

interface Notice {
  kind: 'error' | 'success' | 'info';
  text: string;
  needsLogin?: boolean;
}

const buttonStyle = 'rounded-md bg-blue-600 px-5 py-3 font-medium text-white hover:bg-blue-700 '
  + 'focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 '
  + 'disabled:cursor-not-allowed disabled:opacity-50';

const ReportPage: React.FC = () => {
  const { keycloak, initialized } = useKeycloak();
  const [dateFrom, setDateFrom] = useState(() => yesterdayUtc());
  const [dateTo, setDateTo] = useState(() => yesterdayUtc());
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const inFlight = useRef(false);

  const login = async () => {
    try {
      await keycloak.login({ redirectUri: window.location.origin + '/', prompt: 'login' });
    } catch {
      setNotice({ kind: 'error', text: 'Не удалось открыть страницу входа. Повторите попытку.' });
    }
  };

  const logout = async () => {
    try {
      await keycloak.logout({ redirectUri: window.location.origin + '/' });
    } catch {
      setNotice({ kind: 'error', text: 'Не удалось завершить сеанс. Повторите попытку.' });
    }
  };

  const downloadReport = async (event: React.FormEvent) => {
    event.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    setNotice(null);
    try {
      const report = await requestReport(keycloak, { dateFrom, dateTo });
      downloadReportFile(report);
      setNotice(report.status === 'empty'
        ? {
          kind: 'info',
          text: 'Период обработан, но телеметрии по вашим протезам нет. '
            + 'Пустой JSON-отчёт подготовлен к скачиванию. '
            + 'Отсутствие телеметрии не означает отсутствие активности протеза.',
        }
        : {
          kind: 'success',
          text: 'Отчёт за ' + report.period.date_from + ' — ' + report.period.date_to
            + ' подготовлен к скачиванию. Строк: ' + report.row_count + '.',
        });
    } catch (error) {
      setNotice({
        kind: 'error',
        text: error instanceof ReportError ? error.message : 'Не удалось скачать отчёт. Повторите попытку.',
        needsLogin: error instanceof ReportError && error.needsLogin,
      });
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  };

  if (!initialized) {
    return <main className="flex min-h-screen items-center justify-center bg-gray-100">
      <p role="status">Проверяем вход…</p>
    </main>;
  }

  const noticeStyle = notice?.kind === 'error' ? 'bg-red-50 text-red-800'
    : notice?.kind === 'success' ? 'bg-green-50 text-green-800' : 'bg-blue-50 text-blue-900';

  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-100 px-4 py-8 text-gray-900">
      <section aria-labelledby="report-title" className="w-full max-w-xl rounded-xl bg-white p-6 shadow-md sm:p-8">
        <p className="mb-2 text-sm font-semibold tracking-wide text-blue-700">BionicPRO</p>
        <h1 id="report-title" className="text-2xl font-bold">Отчёты о работе протезов</h1>

        {keycloak.authenticated ? (
          <>
            <div className="mt-3 flex items-center justify-between gap-4 text-sm">
              <p className="break-all text-gray-600">
                Вы вошли: {keycloak.idTokenParsed?.preferred_username || 'пользователь'}
              </p>
              <button type="button" onClick={logout} disabled={loading}
                className="rounded px-2 py-1 text-blue-700 underline focus:ring-2 focus:ring-blue-500 disabled:opacity-50">
                Выйти
              </button>
            </div>
            <p id="period-hint" className="mt-6 text-sm leading-6 text-gray-600">
              Выберите до 31 завершённого дня. Обе даты включены в отчёт, границы суток — UTC.
              Скачиваются данные только по вашим протезам.
            </p>
            <form onSubmit={downloadReport} noValidate aria-busy={loading} className="mt-5">
              <fieldset disabled={loading} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <legend className="sr-only">Период отчёта</legend>
                <div>
                  <label htmlFor="date-from" className="mb-2 block text-sm font-medium">Дата начала</label>
                  <input id="date-from" name="date_from" type="date" required min="1970-01-01" max={yesterdayUtc()}
                    aria-describedby="period-hint" value={dateFrom}
                    onChange={(event) => { setDateFrom(event.target.value); setNotice(null); }}
                    className="block w-full min-w-0 rounded-md border border-gray-300 bg-white px-3 py-3 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200 disabled:bg-gray-100" />
                </div>
                <div>
                  <label htmlFor="date-to" className="mb-2 block text-sm font-medium">Дата окончания</label>
                  <input id="date-to" name="date_to" type="date" required min="1970-01-01" max={yesterdayUtc()}
                    aria-describedby="period-hint" value={dateTo}
                    onChange={(event) => { setDateTo(event.target.value); setNotice(null); }}
                    className="block w-full min-w-0 rounded-md border border-gray-300 bg-white px-3 py-3 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200 disabled:bg-gray-100" />
                </div>
              </fieldset>
              <button type="submit" disabled={loading} className={buttonStyle + ' mt-6 w-full'}>
                {loading ? 'Получаем отчёт…' : 'Скачать отчёт (JSON)'}
              </button>
            </form>
            <p className="mt-3 text-xs leading-5 text-gray-500">
              Данные обновляются ежедневно. За ещё не подготовленные дни отчёт будет доступен позже.
            </p>
          </>
        ) : (
          <div className="mt-6">
            <p className="mb-5 text-gray-600">Войдите, чтобы скачать отчёт по своим протезам.</p>
            <button type="button" onClick={login} className={buttonStyle}>Войти</button>
          </div>
        )}

        {notice && (
          <div role={notice.kind === 'error' ? 'alert' : 'status'}
            className={'mt-5 rounded-md p-4 text-sm leading-6 ' + noticeStyle}>
            <p>{notice.text}</p>
            {notice.needsLogin && keycloak.authenticated && (
              <button type="button" onClick={login} className="mt-2 rounded font-semibold underline focus:ring-2 focus:ring-blue-500">
                Войти снова
              </button>
            )}
          </div>
        )}
      </section>
    </main>
  );
};

export default ReportPage;
