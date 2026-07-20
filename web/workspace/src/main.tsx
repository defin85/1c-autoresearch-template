import React from 'react';
import ReactDOM from 'react-dom/client';
import { bootstrap } from './api';
import { App } from './App';

bootstrap().finally(() => ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>));
