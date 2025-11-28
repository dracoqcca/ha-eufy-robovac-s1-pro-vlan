import logging

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .const import DOMAIN, CONF_MANUAL_DEVICES, CONF_DEVICE_IP
from .eufy_local_id_grabber.clients import EufyHomeSession, TuyaAPISession

logger = logging.getLogger(__name__)

EUFY_LOGIN_SCHEMA = vol.Schema({
    vol.Required("username"): str, 
    vol.Required("password"): str
})

MANUAL_DEVICE_SCHEMA = vol.Schema({
    vol.Optional("add_manual_devices", default=False): bool,
})


class EufyVacuumConfigFlow(ConfigFlow, domain=DOMAIN):
    def __init__(self):
        """Initialize the config flow."""
        self._username = None
        self._password = None
        self._user_info = None
        self._devices = None

    async def async_step_user(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        errors = {}

        if user_input is not None:
            username = user_input["username"]
            password = user_input["password"]

            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()

            client = EufyHomeSession(username, password)

            try:
                user_info = await self.hass.async_add_executor_job(client.get_user_info)
                logger.debug("Eufy user info: %s", user_info)

                # Store credentials and user info for next steps
                self._username = username
                self._password = password
                self._user_info = user_info

                # Get devices from Tuya API
                tuya_session = TuyaAPISession(username=f'eh-{user_info["id"]}', country_code=user_info["phone_code"])
                homes = await self.hass.async_add_executor_job(tuya_session.list_homes)
                
                devices = []
                for home in homes:
                    devices_for_home = await self.hass.async_add_executor_job(tuya_session.list_devices, home["groupId"])
                    for device in devices_for_home:
                        devices.append({
                            "name": device.get("name", f"Device {device['devId']}"),
                            "device_id": device["devId"],
                            "local_key": device["localKey"],
                            "home_id": home["groupId"]
                        })

                self._devices = devices

                if devices:
                    return await self.async_step_manual_config()
                else:
                    # No devices found, just create entry with credentials
                    return self.async_create_entry(
                        title=username,
                        data={CONF_EMAIL: username, CONF_PASSWORD: password},
                    )

            except Exception as e:
                logger.exception("Error when logging in with %s", username)
                errors["username"] = errors["password"] = "Username or password is incorrect"

        return self.async_show_form(step_id="user", data_schema=EUFY_LOGIN_SCHEMA, errors=errors)

    async def async_step_manual_config(self, user_input: dict | None = None) -> data_entry_flow.FlowResult:
        """Handle manual device configuration step."""
        if user_input is not None:
            if not user_input.get("add_manual_devices", False):
                # User chose not to add manual devices, create entry with auto-discovery
                return self.async_create_entry(
                    title=self._username,
                    data={CONF_EMAIL: self._username, CONF_PASSWORD: self._password},
                )
            else:
                # User wants to configure manual devices
                return await self.async_step_device_list()

        # Show devices found and ask if user wants to configure manual IPs
        device_info = "\n".join([f"- {device['name']} (ID: {device['device_id']})" for device in self._devices])
        
        return self.async_show_form(
            step_id="manual_config",
            data_schema=MANUAL_DEVICE_SCHEMA,
            description_placeholders={"devices": device_info}
        )

    async def async_step_device_list(self, user_input: dict | None = None) -> data_entry_flow.FlowResult:
        """Handle manual IP configuration for each device."""
        if user_input is not None:
            # Process the manual device configurations
            manual_devices = {}
            
            for device in self._devices:
                ip_key = f"device_ip_{device['device_id']}"
                if ip_key in user_input and user_input[ip_key].strip():
                    manual_devices[device['device_id']] = {
                        "ip": user_input[ip_key].strip(),
                        "name": device['name'],
                        "local_key": device['local_key'],
                        "home_id": device['home_id']
                    }

            # Create the config entry with manual device configurations
            data = {
                CONF_EMAIL: self._username, 
                CONF_PASSWORD: self._password
            }
            
            if manual_devices:
                data[CONF_MANUAL_DEVICES] = manual_devices

            return self.async_create_entry(title=self._username, data=data)

        # Build schema for manual IP input for each device
        schema_dict = {}
        for device in self._devices:
            schema_dict[vol.Optional(f"device_ip_{device['device_id']}")] = str

        device_schema = vol.Schema(schema_dict)
        
        # Create description with device info
        device_info = "\n".join([
            f"- {device['name']} (ID: {device['device_id']})" 
            for device in self._devices
        ])

        return self.async_show_form(
            step_id="device_list",
            data_schema=device_schema,
            description_placeholders={"devices": device_info}
        )
