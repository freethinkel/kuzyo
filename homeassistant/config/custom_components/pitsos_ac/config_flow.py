from homeassistant.config_entries import ConfigFlow

from . import DOMAIN


class PitsosAcFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_import(self, data):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Pitsos air conditioners", data=data)
