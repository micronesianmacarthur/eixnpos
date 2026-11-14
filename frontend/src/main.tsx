import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './theme.css';
import AppRouter from './AppRouter';
import { ThemeProvider } from './ThemeProvider';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <AppRouter />
    </ThemeProvider>
  </StrictMode>,
);
