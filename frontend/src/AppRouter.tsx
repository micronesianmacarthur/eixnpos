import { StrictMode } from 'react';
import { BrowserRouter } from 'react-router-dom';
import { Routes, Route } from 'react-router-dom';
import './index.css';
import App from './App';
import LoginPage from './LoginPage';
import BusinessInfoForm from './BusinessInfoForm';
import Sidebar from './Sidebar';
import ThemeToggle from './ThemeToggle';

function AppRouter() {
  return (
    <BrowserRouter>
      <StrictMode>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="/login" element={<LoginPage />} />
        </Routes>
      </StrictMode>
    </BrowserRouter>
  );
}

export default AppRouter;