from generators.claims import ClaimsGenerator
from generators.customer_kyc import CustomerKycGenerator
from generators.garage_bills import GarageBillsGenerator
from generators.policy import PolicyGenerator
from generators.telematics import TelematicsGenerator

GENERATORS = {
    "telematics": TelematicsGenerator,
    "policy": PolicyGenerator,
    "claims": ClaimsGenerator,
    "garage_bills": GarageBillsGenerator,
    "customer_kyc": CustomerKycGenerator,
}
