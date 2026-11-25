import { NotFoundMessage } from '@/shell/SystemPage';

/** K1 inside the app shell: call `notFound()` from a page when the record does not exist. */
export default function NotFound() {
  return <NotFoundMessage inline />;
}
