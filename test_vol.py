import voluptuous as vol  
schema = vol.Schema({vol.Optional('retention', default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=100))})  
print('OK')  
