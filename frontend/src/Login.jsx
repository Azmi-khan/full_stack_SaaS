import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'

function Login() {
  //  Set up memory for our form inputs
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  
  const navigate = useNavigate()

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
    <div style={{ padding: '20px', maxWidth: '400px', margin: '0 auto' }}>
      <h2>Sign In</h2>
      
      {/* Show a red error message if login fails */}
      {error && <p style={{ color: 'red' }}>{error}</p>}
      
      <form onSubmit={handleLogin} style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
        <div>
          <label style={{ display: 'block', marginBottom: '5px' }}>Email</label>
          <input 
            type="email" 
            value={email} 
            onChange={(e) => setEmail(e.target.value)} 
            required 
            style={{ width: '100%', padding: '8px' }}
          />
        </div>
        
        <div>
          <label style={{ display: 'block', marginBottom: '5px' }}>Password</label>
          <input 
            type="password" 
            value={password} 
            onChange={(e) => setPassword(e.target.value)} 
            required 
            style={{ width: '100%', padding: '8px' }}
          />
        </div>
        
        <button type="submit" style={{ padding: '10px', cursor: 'pointer' }}>
          Login
        </button>
      </form>
    </div>
  )
}

export default Login