import { useState } from 'react';
import { useEffect } from 'react';
import './index.css';
import LoginPage from './LoginPage';
import BusinessInfoForm from './BusinessInfoForm';
import Sidebar from './Sidebar';
import ThemeToggle from './ThemeToggle';

function App() {
  const [count, setCount] = useState(0);
  const [darkMode, setDarkMode] = useState(false);

  useEffect(() => {
    const toggleDarkMode = () => {
      const bodyClass = document.body.classList;
      if (darkMode) {
        bodyClass.remove('dark');
      } else {
        bodyClass.add('dark');
      }
    };
    setDarkMode(!darkMode);
    toggleDarkMode();
  }, [darkMode]);

  return (
    <>
      <Sidebar />
      <div style={{ display: 'flex', minHeight: '100vh' }}>
        <div style={{ flex: 1, padding: '2rem' }}>
          <ThemeToggle />
          <h1>Welcome to the Utility POS System</h1>
          <p>This is the main application shell.</p>
          
          <BusinessInfoForm />
          
          <p>Count: {count}</p>
          <button onClick={() => setCount(count + 1)}>Increment</button>
        </div>
      </div>
      <div style={{ width: '250px', borderLeft: '1px solid #ccc', padding: '1rem' }}>
        <Sidebar />
      </div>
      <LoginPage />
    </>
  );
}

export default App;