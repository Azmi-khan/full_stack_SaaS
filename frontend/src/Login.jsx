import { useState } from 'react'
import { useNavigate, useLocation, Link } from 'react-router-dom'
import axios from 'axios'
import './Auth.css'

function Login() {
  //  Set up memory for our form inputs
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  const navigate = useNavigate()
  const location = useLocation()
  const justSignedUp = Boolean(location.state?.justSignedUp)

  //  Create the function that runs when the user clicks "Submit"
  const handleLogin = async (e) => {
    e.preventDefault() // Prevents the page from refreshing
    setError('') // Clear any old errors

    try {
      // FastAPI's OAuth2 expects standard Form Data, not JSON
      const formData = new URLSearchParams()
      formData.append('username', email) // OAuth2 strictly looks for 'username'
      formData.append('password', password)

      // Send the login request to your Python backend
      const response = await axios.post('http://127.0.0.1:8000/login', formData, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
      })

      //  Save the secure token to the browser's local storage
      localStorage.setItem('token', response.data.access_token)

      // Redirect the user to their dashboard
      navigate('/dashboard')

    } catch (err) {
      setError('Invalid email or password. Please try again.')
    }
  }

  //  HTML interface
  return (
    <div className="auth">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="auth-brand-mark">◈</span>
          PERCEPTION<span className="auth-brand-thin">.SYS</span>
        </div>
        <p className="auth-subtitle">Sign in to access the perception console</p>

        {justSignedUp && !error && (
          <p className="auth-success">Account created — sign in below.</p>
        )}
        {error && <p className="auth-error">{error}</p>}

        <form onSubmit={handleLogin} className="auth-form">
          <div className="auth-field">
            <label htmlFor="login-email">Email</label>
            <input
              id="login-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
            />
          </div>

          <div className="auth-field">
            <label htmlFor="login-password">Password</label>
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
          </div>

          <button type="submit" className="auth-submit">
            Sign in
          </button>
        </form>

        <p className="auth-switch">
          Don't have an account? <Link to="/signup">Sign up</Link>
        </p>
      </div>
    </div>
  )
}

export default Login