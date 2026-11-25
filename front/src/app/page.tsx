import { redirect } from 'next/navigation';

/** The app starts at Proyectos; without a session the app layout sends the user to /login. */
export default function Home() {
  redirect('/proyectos');
}
