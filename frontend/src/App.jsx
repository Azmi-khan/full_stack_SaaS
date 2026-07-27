import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Login from './Login'
import Dashboard from './Dashboard'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* if a user goes to the root path, redirect them to the login page */}
        <Route path="/" element={<Navigate to="/login" />} />

        {/* define the two main routes for the application */}
        <Route path="/login" element={<Login />} />
        <Route path="/dashboard" element={<Dashboard />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App